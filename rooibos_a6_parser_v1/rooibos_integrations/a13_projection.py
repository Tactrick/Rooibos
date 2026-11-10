"""Provider-neutral projections from A1/A2/A6 canonical records into A13 evidence."""
from __future__ import annotations
from dataclasses import asdict
import json
from typing import Iterable, Mapping
from rooibos_a13.models import EvidenceRecord, SourcePolicy, canonical_hash
from rooibos_a13.validator import assess_record
from rooibos_a13.governance import decide_snapshot
from rooibos_a13.storage import store_assessment, store_decision
from rooibos_a13.read_gate import snapshot_content_hash


def _evidence(record_id, dataset_type, chain, mint, source_id, endpoint_id, run_id,
              raw_response_id, observed_at, normalization_version, payload, source_record_index):
    return EvidenceRecord(record_id, dataset_type, chain, mint, source_id, endpoint_id,
                          run_id, raw_response_id, observed_at, normalization_version,
                          canonical_hash(payload), payload, source_record_index)


def project_a1(observation):
    c, m = observation.context, observation.metadata
    payload = {"chain": c.chain, "mint": c.mint, **asdict(m)}
    return _evidence("a1:" + observation.observation_key, "a1_discovery", c.chain, c.mint,
                     c.source_id, c.endpoint_id, c.run_id, c.raw_response_id, c.observed_at,
                     c.normalization_version, payload, c.source_record_index)


def project_a2(metric):
    value = metric.numeric_value if metric.numeric_value is not None else metric.text_value
    payload = {"metric": metric.metric_code, "value": value, "unit": metric.unit,
               "measurement_kind": metric.measurement_kind, "market_scope": metric.market_scope,
               "market_id": metric.market_id, "value_status": metric.value_status,
               "quote_currency": metric.quote_currency, "window_seconds": metric.window_seconds,
               "metadata": json.loads(metric.metadata_json)}
    return _evidence("a2:" + metric.observation_id, "a2_market", metric.chain,
                     metric.mint_address, metric.source_id, metric.endpoint_id, metric.run_id,
                     metric.raw_response_id, metric.observed_at, metric.normalization_version,
                     payload, metric.source_record_index)


def project_a6(candle, *, observed_at: str):
    from rooibos_a6.parser import INTERVAL_MS
    interval_ms = INTERVAL_MS.get(candle.interval)
    status = "unknown" if candle.candle_status == "unconfirmed" else candle.candle_status
    payload = {"open_time_ms": candle.open_time_ms, "open": candle.open,
               "high": candle.high, "low": candle.low, "close": candle.close,
               "volume": candle.volume, "interval_ms": interval_ms,
               "candle_status": status, "volume_unit": candle.volume_unit,
               "market_id": candle.market_id, "interval": candle.interval}
    identity = canonical_hash({"chain": candle.chain, "mint": candle.mint_address,
                               "market_id": candle.market_id, "interval": candle.interval,
                               "open_time_ms": candle.open_time_ms, "source_id": candle.source_id,
                               "raw_response_id": candle.raw_response_ref,
                               "source_record_index": candle.source_record_index})
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError("A6 observed_at must be supplied from collection/raw-response provenance; candle open time is not observation time")
    return _evidence("a6:" + identity, "a6_ohlcv", candle.chain, candle.mint_address,
                     candle.source_id, candle.endpoint_id, candle.collection_run_id,
                     candle.raw_response_ref, observed_at, candle.normalisation_version,
                     payload, candle.source_record_index)



def project_a6_from_storage(conn, candle_id: str):
    """Project every persisted raw observation supporting one canonical A6 candle.

    A canonical candle is deduplicated, but its evidence is not. Each immutable
    candle/raw-response/source-record relationship becomes a separate A13
    evidence record so snapshot membership and raw-link authorization retain
    the complete provenance set.
    """
    rows = conn.execute("""
        SELECT c.candle_id, a.chain, a.token_address AS mint_address,
               c.market_id, c.interval, c.open_time_ms, c.open, c.high, c.low,
               c.close, c.volume, c.candle_status, c.volume_unit,
               c.normalization_version, l.source_id, l.endpoint_id, l.run_id,
               l.raw_response_id, l.source_record_index, rr.retrieved_at
        FROM ohlcv_candles c
        JOIN assets a ON a.asset_id=c.asset_id
        JOIN a6_candle_evidence_links l ON l.candle_id=c.candle_id
        JOIN raw_responses rr ON rr.raw_response_id=l.raw_response_id
        WHERE c.candle_id=?
        ORDER BY rr.retrieved_at, l.raw_response_id, l.source_record_index
    """, (candle_id,)).fetchall()
    if not rows:
        raise ValueError("A6 candle has no persisted raw-evidence links")

    from rooibos_a6.parser import INTERVAL_MS
    records = []
    for row in rows:
        status = "unknown" if row["candle_status"] == "unconfirmed" else row["candle_status"]
        payload = {
            "open_time_ms": row["open_time_ms"], "open": row["open"],
            "high": row["high"], "low": row["low"], "close": row["close"],
            "volume": row["volume"], "interval_ms": INTERVAL_MS.get(row["interval"]),
            "candle_status": status, "volume_unit": row["volume_unit"],
            "market_id": row["market_id"], "interval": row["interval"],
        }
        record_id = "a6:" + canonical_hash({
            "candle_id": row["candle_id"],
            "raw_response_id": row["raw_response_id"],
            "source_record_index": row["source_record_index"],
        })
        records.append(_evidence(
            record_id, "a6_ohlcv", row["chain"], row["mint_address"],
            row["source_id"], row["endpoint_id"], row["run_id"],
            row["raw_response_id"], row["retrieved_at"],
            row["normalization_version"], payload, row["source_record_index"],
        ))
    return records



def publish_evidence_snapshot(conn, records, *, snapshot_id: str,
                              source_policies: Mapping[str, SourcePolicy],
                              assessed_at: str, decided_at: str, effective_at: str,
                              actor_id: str, reason: str, allow_warnings: bool = False,
                              expected_record_ids: set[str] | None = None):
    """Shared atomic A13 publication boundary for A1, A2 and A6 projections."""
    from rooibos_a13.lifecycle import register_snapshot_for_records

    rows = list(records)
    if not rows or len({r.record_id for r in rows}) != len(rows):
        raise ValueError("A13 publication requires non-empty unique evidence records")
    owns_transaction = not conn.in_transaction
    conn.execute("SAVEPOINT a13_publish_evidence_snapshot")
    try:
        assessments, decision = assess_and_store_snapshot(
            conn, rows, snapshot_id=snapshot_id, source_policies=source_policies,
            assessed_at=assessed_at, decided_at=decided_at,
            allow_warnings=allow_warnings, expected_record_ids=expected_record_ids,
            commit=False,
        )
        if decision.dataset_status not in ("approved_for_research", "approved_with_warnings"):
            conn.execute("RELEASE SAVEPOINT a13_publish_evidence_snapshot")
            if owns_transaction:
                conn.commit()
            return rows, assessments, decision, None

        register_snapshot_for_records(
            conn, snapshot_id=snapshot_id, event_id=snapshot_id + ":registered",
            effective_at=effective_at, actor_id=actor_id, reason=reason,
            records=rows, commit=False,
        )
        from rooibos_a13.read_gate import authorize_snapshot
        grant = authorize_snapshot(
            conn, snapshot_id=snapshot_id, records=rows,
            expected_snapshot_hash=snapshot_content_hash(rows),
            expected_assessments_hash=decision.assessments_hash,
            allowed_policy_versions={decision.policy_version},
            allow_warnings=allow_warnings, now=effective_at,
        )
        conn.execute("RELEASE SAVEPOINT a13_publish_evidence_snapshot")
        if owns_transaction:
            conn.commit()
        return rows, assessments, decision, grant
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT a13_publish_evidence_snapshot")
        conn.execute("RELEASE SAVEPOINT a13_publish_evidence_snapshot")
        if owns_transaction:
            conn.rollback()
        raise


def project_a2_from_storage(conn, observation_ids):
    """Rebuild exact A2 evidence records from immutable persisted observations."""
    from rooibos_a2.normalizer import NormalizedMetric

    ids = list(observation_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("A2 snapshot requires a non-empty unique observation ID set")
    placeholders = ",".join("?" for _ in ids)
    sql = "SELECT * FROM market_observations WHERE observation_id IN (" + placeholders + ")"
    rows = conn.execute(sql, ids).fetchall()
    by_id = {row["observation_id"]: row for row in rows}
    if set(by_id) != set(ids):
        raise ValueError("A2 snapshot membership does not match persisted observations")

    projected = []
    for observation_id in ids:
        row = by_id[observation_id]
        metric = NormalizedMetric(
            row["observation_id"], row["chain"], row["mint_address"], row["market_id"],
            row["pool_address"], row["market_scope"], row["metric_code"],
            row["numeric_value"], row["text_value"], row["value_status"], row["unit"],
            row["quote_currency"], row["window_seconds"], row["measurement_kind"],
            row["observed_at"], row["source_id"], row["endpoint_id"], row["run_id"],
            row["raw_response_id"], row["source_record_index"], row["normalization_version"],
            row["metadata_json"], row["content_sha256"],
        )
        projected.append(project_a2(metric))
    return projected


def publish_a2_snapshot_from_storage(conn, observation_ids, *, snapshot_id: str,
                                     source_policies: Mapping[str, SourcePolicy],
                                     assessed_at: str, decided_at: str, effective_at: str,
                                     actor_id: str, reason: str,
                                     allow_warnings: bool = False,
                                     expected_record_ids: set[str] | None = None):
    """Publish persisted A2 observations through the shared A13 read boundary."""
    records = project_a2_from_storage(conn, observation_ids)
    return publish_evidence_snapshot(
        conn, records, snapshot_id=snapshot_id, source_policies=source_policies,
        assessed_at=assessed_at, decided_at=decided_at, effective_at=effective_at,
        actor_id=actor_id, reason=reason, allow_warnings=allow_warnings,
        expected_record_ids=expected_record_ids,
    )


def publish_a6_snapshot_from_storage(conn, candle_ids, *, snapshot_id: str,
                                     source_policies: Mapping[str, SourcePolicy],
                                     assessed_at: str, decided_at: str,
                                     effective_at: str, actor_id: str, reason: str,
                                     allow_warnings: bool = False,
                                     expected_record_ids: set[str] | None = None):
    """Atomically assess, decide, link, register and verify one A6 snapshot.

    The savepoint spans the complete publication boundary. Any exception after
    assessment (including lifecycle registration or the final read gate) rolls
    back assessments, decision, lifecycle event and raw-evidence links together.
    """
    ids = list(candle_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("A6 snapshot requires a non-empty unique candle ID set")
    records = []
    for candle_id in ids:
        records.extend(project_a6_from_storage(conn, candle_id))
    return publish_evidence_snapshot(
        conn, records, snapshot_id=snapshot_id, source_policies=source_policies,
        assessed_at=assessed_at, decided_at=decided_at, effective_at=effective_at,
        actor_id=actor_id, reason=reason, allow_warnings=allow_warnings,
        expected_record_ids=expected_record_ids,
    )


def assess_and_store_snapshot(conn, records: Iterable[EvidenceRecord], *, snapshot_id: str,
                              source_policies: Mapping[str, SourcePolicy], assessed_at: str,
                              decided_at: str, allow_warnings: bool = False,
                              conflict_record_ids: set[str] | None = None,
                              expected_record_ids: set[str] | None = None,
                              commit: bool = True):
    rows = list(records)
    if not rows:
        raise ValueError("snapshot cannot be empty")
    dataset_types = {r.dataset_type for r in rows}
    if len(dataset_types) != 1:
        raise ValueError("one A13 snapshot must contain exactly one dataset type")
    assessments = [assess_record(r, source_policies, assessed_at=assessed_at) for r in rows]
    decision = decide_snapshot(snapshot_id, next(iter(dataset_types)), assessments,
                               decided_at=decided_at, allow_warnings=allow_warnings,
                               conflict_record_ids=conflict_record_ids,
                               expected_record_ids=expected_record_ids,
                               snapshot_hash=snapshot_content_hash(rows))
    # Assessments and their snapshot decision are one publication unit. A failed
    # immutable decision insert must not leave orphaned assessment rows behind.
    conn.execute("SAVEPOINT a13_assess_store_snapshot")
    try:
        for a in assessments:
            ah = canonical_hash({"record_id": a.record_id, "status": a.status,
                                 "findings": [asdict(f) for f in a.findings],
                                 "assessed_at": a.assessed_at, "validator_version": a.validator_version})
            store_assessment(conn, a, "assessment:" + ah[:32], ah, commit=False)
        store_decision(conn, decision, commit=False)
        conn.execute("RELEASE SAVEPOINT a13_assess_store_snapshot")
        if commit:
            conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT a13_assess_store_snapshot")
        conn.execute("RELEASE SAVEPOINT a13_assess_store_snapshot")
        raise
    return assessments, decision
