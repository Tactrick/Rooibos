"""Publication-only recovery for normalized A1/A2/A6 collector runs.

This module never decodes provider payloads, invokes normalizers, captures
responses, or creates canonical observations/candles. It republishes only the
exact immutable membership recorded before the original A13 attempt.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from rooibos_db.database import _connect
from rooibos_a13.models import SourcePolicy
from .framework import CollectorFramework
from .record_manifest import load_verified_manifest


def recover_a13_publication(
    framework: CollectorFramework, *, run_id: str, raw_response_id: str,
    snapshot_id: str, source_policies: Mapping[str, SourcePolicy],
    assessed_at: str, decided_at: str, effective_at: str,
    actor_id: str, reason: str, allow_warnings: bool = False,
    expected_record_ids: set[str] | None = None,
):
    """Retry A13 for a run explicitly marked as a publication-stage failure."""
    conn = _connect(framework.db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        run = conn.execute(
            """SELECT cr.status, cr.source_id, cr.endpoint_id,
                      cr.publication_failed_at, c.module_code
               FROM collection_runs cr
               JOIN collectors c ON c.collector_id=cr.collector_id
               WHERE cr.run_id=?""",
            (run_id,),
        ).fetchone()
        if run is None:
            raise ValueError("unknown collection run")
        if run["status"] != "validating" or not run["publication_failed_at"]:
            raise ValueError("only a run explicitly marked with an A13 publication failure is recoverable")
        module = run["module_code"]
        if module not in {"A1", "A2", "A6"}:
            raise ValueError("publication recovery supports only A1, A2 and A6")
        raw = conn.execute(
            """SELECT run_id, source_id, endpoint_id
               FROM raw_responses WHERE raw_response_id=?""",
            (raw_response_id,),
        ).fetchone()
        if raw is None or (
            raw["run_id"], raw["source_id"], raw["endpoint_id"]
        ) != (run_id, run["source_id"], run["endpoint_id"]):
            raise ValueError("recovery raw-response provenance does not match the collection run")
        if conn.execute(
            "SELECT COUNT(*) FROM raw_responses WHERE run_id=?", (run_id,)
        ).fetchone()[0] != 1:
            raise ValueError("recovery requires the original single-response run")

        manifest = load_verified_manifest(
            conn, run_id=run_id, raw_response_id=raw_response_id,
            collector_module=module,
        )
        if module == "A1":
            rows = conn.execute(
                """SELECT observation_key AS record_id, source_record_index
                   FROM token_discovery_observations
                   WHERE run_id=? AND raw_response_id=?""",
                (run_id, raw_response_id),
            ).fetchall()
            actual = {i: [] for i in range(len(manifest))}
            for row in rows:
                actual[row["source_record_index"]].append(row["record_id"])
            actual = {i: tuple(sorted(v)) for i, v in actual.items()}
            if actual != manifest:
                raise ValueError("A1 persisted observations do not match the immutable source-record manifest")
            from rooibos_integrations.a13_projection import publish_a1_snapshot_from_storage
            ids = sorted(record_id for values in manifest.values() for record_id in values)
            published = publish_a1_snapshot_from_storage(
                conn, ids, snapshot_id=snapshot_id, source_policies=source_policies,
                assessed_at=assessed_at, decided_at=decided_at, effective_at=effective_at,
                actor_id=actor_id, reason=reason, allow_warnings=allow_warnings,
                expected_record_ids=expected_record_ids,
            )
        elif module == "A2":
            rows = conn.execute(
                """SELECT observation_id AS record_id, source_record_index
                   FROM market_observations
                   WHERE run_id=? AND raw_response_id=?""",
                (run_id, raw_response_id),
            ).fetchall()
            actual = {i: [] for i in range(len(manifest))}
            for row in rows:
                actual[row["source_record_index"]].append(row["record_id"])
            actual = {i: tuple(sorted(v)) for i, v in actual.items()}
            if actual != manifest:
                raise ValueError("A2 persisted observations do not match the immutable source-record manifest")
            from rooibos_integrations.a13_projection import publish_a2_snapshot_from_storage
            ids = sorted(record_id for values in manifest.values() for record_id in values)
            published = publish_a2_snapshot_from_storage(
                conn, ids, snapshot_id=snapshot_id, source_policies=source_policies,
                assessed_at=assessed_at, decided_at=decided_at, effective_at=effective_at,
                actor_id=actor_id, reason=reason, allow_warnings=allow_warnings,
                expected_record_ids=expected_record_ids,
            )
        else:
            rows = conn.execute(
                """SELECT source_record_index, candle_id
                   FROM a6_candle_evidence_links
                   WHERE run_id=? AND raw_response_id=?""",
                (run_id, raw_response_id),
            ).fetchall()
            actual = {i: [] for i in range(len(manifest))}
            for row in rows:
                actual[row["source_record_index"]].append(
                    f"{row['source_record_index']}:{row['candle_id']}"
                )
            actual = {i: tuple(sorted(v)) for i, v in actual.items()}
            if actual != manifest:
                raise ValueError("A6 candle evidence links do not match the immutable source-record manifest")
            from rooibos_integrations.a13_projection import publish_a6_snapshot_from_storage
            candle_ids = sorted({
                value.split(":", 1)[1]
                for values in manifest.values() for value in values
            })
            published = publish_a6_snapshot_from_storage(
                conn, candle_ids, snapshot_id=snapshot_id, source_policies=source_policies,
                assessed_at=assessed_at, decided_at=decided_at, effective_at=effective_at,
                actor_id=actor_id, reason=reason, allow_warnings=allow_warnings,
                expected_record_ids=expected_record_ids,
            )

        records, assessments, decision, grant = published
        if grant is None:
            conn.execute(
                """UPDATE collection_runs
                   SET status='quarantined', completed_at=?, error_summary=?,
                       publication_failure_summary=NULL
                   WHERE run_id=? AND status='validating' AND publication_failed_at IS NOT NULL""",
                (datetime.now(timezone.utc).isoformat(),
                 f"A13 recovery decision quarantined snapshot: {decision.dataset_status}", run_id),
            )
        else:
            framework.complete_after_a13_publication(
                run_id, grant=grant, records=records, connection=conn,
            )
            conn.execute(
                """UPDATE collection_runs
                   SET publication_failed_at=NULL, publication_failure_summary=NULL
                   WHERE run_id=? AND status='completed'""",
                (run_id,),
            )
        conn.commit()
        return {
            "run_id": run_id, "collector_module": module,
            "published": grant is not None, "records": records,
            "assessments": assessments, "decision": decision, "grant": grant,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
