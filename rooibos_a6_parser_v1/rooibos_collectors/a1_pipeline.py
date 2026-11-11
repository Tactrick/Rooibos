"""A1 raw-response-to-A13 orchestration for token discovery evidence."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Iterable, Mapping

from rooibos_db.database import _connect
from rooibos_a1.models import DiscoveryObservation
from rooibos_a1.storage import store_observations
from rooibos_a13.models import SourcePolicy
from rooibos_integrations.a13_projection import publish_a1_snapshot_from_storage

from .framework import CollectorFramework


@dataclass(frozen=True)
class A1ResponseProvenance:
    run_id: str
    source_id: str
    endpoint_id: str
    raw_response_id: str
    retrieved_at: str


@dataclass(frozen=True)
class A1PipelineResult:
    observations: tuple[DiscoveryObservation, ...]
    inserted_count: int
    observation_keys: tuple[str, ...]
    assessments: tuple[Any, ...]
    decision: Any | None
    grant: Any | None

    @property
    def published(self) -> bool:
        return self.grant is not None


def validate_a1_source_record_coverage(observations: Iterable[DiscoveryObservation]) -> None:
    """Require exact one-to-one coverage of the captured provider record array."""
    rows = list(observations)
    if not rows:
        raise ValueError("A1 source response contains no decoded records")
    counts = [row.context.source_record_count for row in rows]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in counts):
        raise ValueError("A1 decoder must supply the original source_record_count for every record")
    if len(set(counts)) != 1:
        raise ValueError("A1 decoded records disagree on original source_record_count")
    indices = [row.context.source_record_index for row in rows]
    if len(set(indices)) != len(indices):
        raise ValueError("A1 decoder returned duplicate original source_record_index values")
    if len(rows) != counts[0] or set(indices) != set(range(counts[0])):
        raise ValueError("A1 decoder omitted or altered original response records; source provenance is incomplete")


def process_a1_raw_response(
    framework: CollectorFramework,
    *,
    run_id: str,
    raw_response_id: str,
    decode_payload: Callable[
        [Any, A1ResponseProvenance], Iterable[DiscoveryObservation]
    ],
    snapshot_id: str,
    source_policies: Mapping[str, SourcePolicy],
    assessed_at: str,
    decided_at: str,
    effective_at: str,
    actor_id: str,
    reason: str,
    allow_warnings: bool = False,
    expected_record_ids: set[str] | None = None,
) -> A1PipelineResult:
    """Normalize, atomically persist and A13-gate one immutable A1 response.

    The adapter must explicitly map provider fields using A1's normalizer and
    return DiscoveryObservation objects. Provenance is checked against the
    immutable response before batch persistence. No network or trade API is used.
    """
    db_path = framework.db_path
    conn = _connect(db_path)
    try:
        raw = conn.execute(
            """SELECT rr.run_id, rr.source_id, rr.endpoint_id, rr.retrieved_at,
                      rr.payload_json, cr.status, collectors.module_code
               FROM raw_responses rr
               JOIN collection_runs cr ON cr.run_id=rr.run_id
               JOIN collectors ON collectors.collector_id=cr.collector_id
               WHERE rr.raw_response_id=?""",
            (raw_response_id,),
        ).fetchone()
        if raw is None:
            raise ValueError("A1 processing requires an existing captured raw response")
        if raw["module_code"] != "A1":
            raise ValueError("A1 pipeline can process only runs registered to the A1 collector module")
        if raw["run_id"] != run_id:
            raise ValueError("A1 raw response does not belong to the requested collection run")
        if raw["status"] != "raw_captured":
            raise ValueError(f"A1 response must be processed from raw_captured state, not {raw['status']!r}")
        payload = json.loads(raw["payload_json"])
        provenance = A1ResponseProvenance(
            raw["run_id"], raw["source_id"], raw["endpoint_id"],
            raw_response_id, raw["retrieved_at"],
        )
    finally:
        conn.close()

    try:
        observations = tuple(decode_payload(payload, provenance))
        if not observations:
            framework.transition(run_id, "parsed")
            framework.transition(run_id, "normalised")
            framework.transition(run_id, "validating")
            framework.transition(
                run_id, "quarantined",
                error_summary="A1 adapter returned no discovery observations",
            )
            return A1PipelineResult((), 0, (), (), None, None)

        for observation in observations:
            if not isinstance(observation, DiscoveryObservation):
                raise TypeError("A1 provider decoder must return DiscoveryObservation objects")
            context = observation.context
            if (context.run_id, context.source_id, context.endpoint_id, context.raw_response_id) != (
                provenance.run_id, provenance.source_id,
                provenance.endpoint_id, provenance.raw_response_id,
            ):
                raise ValueError("A1 adapter provenance differs from persisted raw response")
        validate_a1_source_record_coverage(observations)

        framework.transition(run_id, "parsed")
        conn = _connect(db_path)
        try:
            inserted = store_observations(conn, observations)
        finally:
            conn.close()
        framework.transition(run_id, "normalised")
        framework.transition(run_id, "validating")

        keys = tuple(sorted({item.observation_key for item in observations}))
        conn = _connect(db_path)
        try:
            records, assessments, decision, grant = publish_a1_snapshot_from_storage(
                conn, keys, snapshot_id=snapshot_id,
                source_policies=source_policies, assessed_at=assessed_at,
                decided_at=decided_at, effective_at=effective_at,
                actor_id=actor_id, reason=reason,
                allow_warnings=allow_warnings,
                expected_record_ids=expected_record_ids,
            )
        finally:
            conn.close()

        if grant is None:
            framework.transition(
                run_id, "quarantined",
                error_summary=f"A13 decision quarantined snapshot: {decision.dataset_status}",
            )
        else:
            framework.transition(run_id, "completed")
        return A1PipelineResult(
            observations, inserted, keys, tuple(assessments), decision, grant,
        )
    except Exception as exc:
        try:
            conn = _connect(db_path)
            try:
                row = conn.execute(
                    "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
                ).fetchone()
            finally:
                conn.close()
            if row and row["status"] == "normalised":
                framework.transition(run_id, "validating")
                framework.transition(run_id, "failed", error_summary=str(exc)[:1000])
            elif row and row["status"] in {"raw_captured", "parsed", "validating", "partial"}:
                framework.transition(run_id, "failed", error_summary=str(exc)[:1000])
        except Exception:
            pass
        raise
