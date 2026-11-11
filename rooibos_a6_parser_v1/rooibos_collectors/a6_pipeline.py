"""A6 raw-response-to-A13 pipeline orchestration.

This is a provider-neutral execution seam: callers supply a pure decoder that
maps an already-captured provider payload into A6's canonical record contract.
It performs no network I/O and cannot invoke trading or wallet operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable, Mapping

from rooibos_db.database import _connect
from rooibos_a6.parser import ParseContext, ParseResult, normalise_records
from rooibos_a6.storage import persist_parse_result
from rooibos_a13.models import SourcePolicy
from rooibos_integrations.a13_projection import publish_a6_snapshot_from_storage

from .framework import CollectorFramework


@dataclass(frozen=True)
class A6PipelineResult:
    """Persisted result of processing one captured response through A13."""

    parse_result: ParseResult
    storage_result: Mapping[str, Any]
    candle_ids: tuple[str, ...]
    assessments: tuple[Any, ...]
    decision: Any | None
    grant: Any | None

    @property
    def published(self) -> bool:
        return self.grant is not None


def process_a6_raw_response(
    framework: CollectorFramework,
    *,
    run_id: str,
    raw_response_id: str,
    parse_context: ParseContext,
    request_identity_fields: Mapping[str, str],
    response_identity_fields: Mapping[str, tuple[str, ...]],
    decode_payload: Callable[[Any], Iterable[Mapping[str, Any]]],
    snapshot_id: str,
    source_policies: Mapping[str, SourcePolicy],
    assessed_at: str,
    decided_at: str,
    effective_at: str,
    actor_id: str,
    reason: str,
    allow_warnings: bool = False,
    expected_record_ids: set[str] | None = None,
) -> A6PipelineResult:
    """Process one immutable captured response through parse, storage and A13.

    `request_identity_fields` explicitly maps ParseContext identity fields to
    captured request parameter names; no provider aliases are inferred.
    `response_identity_fields` maps the independently returned response mint to an explicit nested payload path. A response without that identity is rejected; request parameters alone do not prove response attribution.
    `decode_payload` is the only provider-specific seam. It must be a pure
    transformation and return records using A6's timestamp_ms/open/high/low/
    close/volume contract. The payload is always read from immutable storage,
    never accepted separately from the caller.

    A run is marked completed only after A13 publication and its read gate
    return an authorization grant. Parser/storage conflicts or rejected input
    quarantine the run instead of publishing a partial candle snapshot.
    """
    if parse_context.collection_run_id != run_id:
        raise ValueError("A6 ParseContext run ID does not match requested run")
    if parse_context.raw_response_ref != raw_response_id:
        raise ValueError("A6 ParseContext raw-response ID does not match requested evidence")
    if set(request_identity_fields) != {"mint_address", "interval"} or any(
        not isinstance(value, str) or not value for value in request_identity_fields.values()
    ):
        raise ValueError("request_identity_fields must explicitly map mint_address and interval to captured request parameter names")
    if set(response_identity_fields) != {"mint_address"}:
        raise ValueError("response_identity_fields must explicitly map mint_address to a response payload path")
    response_mint_path = response_identity_fields["mint_address"]
    if not isinstance(response_mint_path, tuple) or not response_mint_path or any(not isinstance(part, str) or not part for part in response_mint_path):
        raise ValueError("response mint identity path must be a non-empty tuple of field names")
    if not snapshot_id or not actor_id or not reason:
        raise ValueError("snapshot_id, actor_id and reason are required")

    db_path = framework.db_path
    conn = _connect(db_path)
    try:
        raw = conn.execute(
            """SELECT rr.run_id, rr.source_id, rr.endpoint_id, rr.request_id,
                      rr.retrieved_at, rr.payload_json, cr.status, collectors.module_code,
                      req.request_params_json
               FROM raw_responses rr
               JOIN collection_requests req ON req.request_id=rr.request_id
               JOIN collection_runs cr ON cr.run_id=rr.run_id
               JOIN collectors ON collectors.collector_id=cr.collector_id
               WHERE rr.raw_response_id=?""",
            (raw_response_id,),
        ).fetchone()
        if raw is None:
            raise ValueError("A6 processing requires an existing captured raw response")
        if (raw["run_id"], raw["source_id"], raw["endpoint_id"]) != (
            run_id, parse_context.source_id, parse_context.endpoint_id
        ):
            raise ValueError("A6 ParseContext provenance does not match persisted raw response")
        if raw["module_code"] != "A6":
            raise ValueError("A6 pipeline can process only runs registered to the A6 collector module")
        if raw["status"] != "raw_captured":
            raise ValueError(f"A6 response must be processed from raw_captured state, not {raw['status']!r}")
        response_count = conn.execute(
            "SELECT COUNT(*) FROM raw_responses WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        if response_count != 1:
            raise ValueError(
                "A6 single-response pipeline requires exactly one captured response per run; "
                "multi-response aggregation is not implemented"
            )
        request_params = json.loads(raw["request_params_json"])
        for context_field, request_field in request_identity_fields.items():
            if request_field not in request_params:
                raise ValueError(
                    f"Captured A6 request is missing mapped identity parameter {request_field!r}"
                )
            if request_params[request_field] != getattr(parse_context, context_field):
                raise ValueError(
                    f"A6 ParseContext {context_field} does not match captured request parameter {request_field!r}"
                )
        payload = json.loads(raw["payload_json"])
        response_identity: Any = payload
        for path_part in response_mint_path:
            if not isinstance(response_identity, Mapping) or path_part not in response_identity:
                raise ValueError(f"Captured A6 response is missing mapped market identity path {'.'.join(response_mint_path)!r}")
            response_identity = response_identity[path_part]
        if response_identity != parse_context.mint_address:
            raise ValueError("A6 response mint identity does not match requested market ParseContext")
    finally:
        conn.close()

    try:
        records = list(decode_payload(payload))
        if any(not isinstance(item, Mapping) for item in records):
            raise TypeError("A6 provider decoder must return mapping records")

        # Publication requires exact coordinates into the captured response.
        # Re-enumerating decoded rows would shift provenance after filtering
        # or reordering, so require a complete, one-to-one source index set.
        if records:
            indices = [item.get("source_record_index") for item in records]
            counts = [item.get("source_record_count") for item in records]
            if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 for i in indices):
                raise ValueError("A6 decoded records must carry non-negative original source_record_index values")
            if any(isinstance(c, bool) or not isinstance(c, int) or c < 0 for c in counts):
                raise ValueError("A6 decoded records must carry the original source_record_count")
            if len(set(indices)) != len(indices):
                raise ValueError("A6 decoded records contain duplicate original source_record_index values")
            if len(set(counts)) != 1:
                raise ValueError("A6 decoded records disagree on original source_record_count")
            source_count = counts[0]
            if len(records) != source_count or set(indices) != set(range(source_count)):
                raise ValueError("A6 decoder omitted or altered original response records; source provenance is incomplete")
        framework.transition(run_id, "parsed")
        parsed = normalise_records(records, parse_context)
        stored = persist_parse_result(
            db_path, result=parsed, raw_response_id=raw_response_id,
        )

        if records:
            from .record_manifest import store_record_manifest
            manifest_conn = _connect(db_path)
            try:
                links = manifest_conn.execute(
                    """SELECT source_record_index, candle_id FROM a6_candle_evidence_links
                       WHERE raw_response_id=? ORDER BY source_record_index, candle_id""",
                    (raw_response_id,),
                ).fetchall()
                membership = {i: [] for i in range(records[0]["source_record_count"])}
                for link in links:
                    membership.setdefault(link["source_record_index"], []).append(
                        f"{link['source_record_index']}:{link['candle_id']}"
                    )
                store_record_manifest(
                    manifest_conn, run_id=run_id, raw_response_id=raw_response_id,
                    collector_module="A6", source_record_count=records[0]["source_record_count"],
                    membership=membership,
                )
            finally:
                manifest_conn.close()

        conn = _connect(db_path)
        try:
            candle_ids = tuple(row[0] for row in conn.execute(
                """SELECT DISTINCT candle_id FROM a6_candle_evidence_links
                   WHERE raw_response_id=? ORDER BY candle_id""",
                (raw_response_id,),
            ).fetchall())
        finally:
            conn.close()

        framework.transition(run_id, "validating")

        # Do not allow an otherwise-valid subset to conceal rejected records,
        # conflicting variants or conflicts with already-persisted candles.
        blocking_parse_findings = (
            parsed.rejected_count > 0
            or parsed.conflict_count > 0
            or stored["storage_conflict_count"] > 0
            or stored["parser_conflict_variant_count"] > 0
        )
        if not candle_ids or blocking_parse_findings:
            framework.transition(
                run_id, "quarantined",
                error_summary=(
                    "A6 output withheld from A13: "
                    f"candles={len(candle_ids)}, rejected={parsed.rejected_count}, "
                    f"parser_conflicts={parsed.conflict_count}, "
                    f"storage_conflicts={stored['storage_conflict_count']}"
                ),
            )
            return A6PipelineResult(parsed, stored, candle_ids, (), None, None)

        conn = _connect(db_path)
        try:
            # Hold the run's write lock across A13 publication and completion.
            conn.execute("BEGIN IMMEDIATE")
            run_state = conn.execute(
                "SELECT status, publication_failed_at FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run_state is None or run_state["status"] != "validating":
                raise ValueError("A13 publication requires a run still in validating state")
            records_a13, assessments, decision, grant = publish_a6_snapshot_from_storage(
                conn, candle_ids, snapshot_id=snapshot_id,
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
                    (datetime.now(timezone.utc).isoformat(),
                     f"A13 decision quarantined snapshot: {decision.dataset_status}", run_id),
                )
            else:
                framework.complete_after_a13_publication(
                    run_id, grant=grant, records=records_a13, connection=conn,
                )
            conn.commit()
        except Exception as publication_exc:
            conn.rollback()
            try:
                framework.mark_publication_failure(run_id, str(publication_exc))
            except Exception:
                pass
            raise
        finally:
            conn.close()
        return A6PipelineResult(
            parsed, stored, candle_ids, tuple(assessments), decision, grant,
        )
    except Exception as exc:
        # Preserve the failure in the run ledger without masking the original
        # processing exception if a transition itself is no longer possible.
        try:
            conn = _connect(db_path)
            try:
                row = conn.execute(
                    "SELECT status, publication_failed_at FROM collection_runs WHERE run_id=?", (run_id,)
                ).fetchone()
            finally:
                conn.close()
            if row and row["status"] == "normalised":
                framework.transition(run_id, "validating")
                framework.transition(run_id, "failed", error_summary=str(exc)[:1000])
            elif row and row["status"] in {"raw_captured", "parsed", "validating", "partial"} and not row["publication_failed_at"]:
                framework.transition(run_id, "failed", error_summary=str(exc)[:1000])
        except Exception:
            pass
        raise
