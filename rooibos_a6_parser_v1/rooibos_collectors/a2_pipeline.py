"""A2 raw-response-to-A13 orchestration using provider-neutral metric mappings."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from rooibos_db.database import _connect
from rooibos_a2.normalizer import (
    A2Finding, A2Result, MarketObservationInput, normalize_observation,
)
from rooibos_a2.storage import persist_a2_result
from rooibos_a13.models import SourcePolicy
from rooibos_integrations.a13_projection import publish_a2_snapshot_from_storage

from .framework import CollectorFramework


@dataclass(frozen=True)
class A2ResponseProvenance:
    run_id: str
    source_id: str
    endpoint_id: str
    raw_response_id: str
    retrieved_at: str


@dataclass(frozen=True)
class A2PipelineResult:
    normalization_result: A2Result
    storage_result: Mapping[str, Any]
    observation_ids: tuple[str, ...]
    assessments: tuple[Any, ...]
    decision: Any | None
    grant: Any | None

    @property
    def published(self) -> bool:
        return self.grant is not None


def validate_a2_source_record_coverage(inputs: Iterable[MarketObservationInput]) -> None:
    """Require exact coverage of source records before metric fan-out."""
    rows = list(inputs)
    if not rows:
        raise ValueError("A2 source response contains no decoded records")
    counts = [row.source_record_count for row in rows]
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in counts):
        raise ValueError("A2 decoder must supply the original source_record_count for every source record")
    if len(set(counts)) != 1:
        raise ValueError("A2 decoded records disagree on original source_record_count")
    indices = [row.source_record_index for row in rows]
    if len(set(indices)) != len(indices):
        raise ValueError("A2 decoder returned duplicate source records")
    if len(rows) != counts[0] or set(indices) != set(range(counts[0])):
        raise ValueError("A2 decoder omitted or altered original response records; source provenance is incomplete")


def process_a2_raw_response(
    framework: CollectorFramework,
    *,
    run_id: str,
    raw_response_id: str,
    decode_payload: Callable[
        [Any, A2ResponseProvenance], Iterable[MarketObservationInput]
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
) -> A2PipelineResult:
    """Normalize one immutable A2 response, persist it, then publish through A13.

    The source adapter only maps provider payload fields to MarketObservationInput
    objects. This function binds their provenance to the persisted raw response,
    stores immutable normalized metrics and gates the resulting snapshot. It
    performs no network requests and no trading operations.
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
            raise ValueError("A2 processing requires an existing captured raw response")
        if raw["module_code"] != "A2":
            raise ValueError("A2 pipeline can process only runs registered to the A2 collector module")
        if raw["run_id"] != run_id:
            raise ValueError("A2 raw response does not belong to the requested collection run")
        if raw["status"] != "raw_captured":
            raise ValueError(f"A2 response must be processed from raw_captured state, not {raw['status']!r}")
        payload = json.loads(raw["payload_json"])
        provenance = A2ResponseProvenance(
            raw["run_id"], raw["source_id"], raw["endpoint_id"],
            raw_response_id, raw["retrieved_at"],
        )
    finally:
        conn.close()

    try:
        inputs = list(decode_payload(payload, provenance))
        if not inputs:
            result = A2Result([], [A2Finding(
                "NO_SOURCE_RECORDS", "error", None,
                "Provider adapter returned no A2 source records",
            )], 0, 0, 0)
        else:
            normalized: list[Any] = []
            findings: list[A2Finding] = []
            received = rejected = 0
            validate_a2_source_record_coverage(inputs)
            for item in inputs:
                if not isinstance(item, MarketObservationInput):
                    raise TypeError("A2 provider decoder must return MarketObservationInput objects")
                if (item.run_id, item.source_id, item.endpoint_id, item.raw_response_id) != (
                    provenance.run_id, provenance.source_id,
                    provenance.endpoint_id, provenance.raw_response_id,
                ):
                    raise ValueError("A2 adapter provenance differs from persisted raw response")
                partial = normalize_observation(item)
                normalized.extend(partial.observations)
                findings.extend(partial.findings)
                received += partial.received_metric_count
                rejected += partial.rejected_metric_count
            result = A2Result(normalized, findings, received, len(normalized), rejected)

        framework.transition(run_id, "parsed")
        stored = persist_a2_result(db_path, result=result)
        framework.transition(run_id, "normalised")
        framework.transition(run_id, "validating")

        observation_ids = tuple(sorted({o.observation_id for o in result.observations}))
        blocking_findings = any(f.severity in {"error", "critical"} for f in result.findings)
        if not observation_ids or result.rejected_metric_count or blocking_findings:
            summary = (
                "A2 output withheld from A13: "
                f"observations={len(observation_ids)}, rejected={result.rejected_metric_count}, "
                f"error_findings={sum(f.severity in {'error', 'critical'} for f in result.findings)}"
            )
            framework.transition(run_id, "quarantined", error_summary=summary)
            return A2PipelineResult(result, stored, observation_ids, (), None, None)

        conn = _connect(db_path)
        try:
            # Hold the run's write lock across A13 publication and the final
            # authorization check/completion. Cancellation cannot split them.
            conn.execute("BEGIN IMMEDIATE")
            run_state = conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run_state is None or run_state["status"] != "validating":
                raise ValueError("A13 publication requires a run still in validating state")
            records, assessments, decision, grant = publish_a2_snapshot_from_storage(
                conn, observation_ids, snapshot_id=snapshot_id,
                source_policies=source_policies, assessed_at=assessed_at,
                decided_at=decided_at, effective_at=effective_at,
                actor_id=actor_id, reason=reason,
                allow_warnings=allow_warnings,
                expected_record_ids=expected_record_ids,
            )
            if grant is None:
                conn.execute(
                    """UPDATE collection_runs
                       SET status='quarantined', completed_at=?, error_summary=?
                       WHERE run_id=? AND status='validating'""",
                    (__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                     f"A13 decision quarantined snapshot: {decision.dataset_status}", run_id),
                )
            else:
                framework.complete_after_a13_publication(
                    run_id, grant=grant, records=records, connection=conn,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return A2PipelineResult(result, stored, observation_ids, tuple(assessments), decision, grant)
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
