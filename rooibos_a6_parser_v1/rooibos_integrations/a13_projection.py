"""Provider-neutral projections from A1/A2/A6 canonical records into A13 evidence."""
from __future__ import annotations
from dataclasses import asdict
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
               "quote_currency": metric.quote_currency, "window_seconds": metric.window_seconds}
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


def assess_and_store_snapshot(conn, records: Iterable[EvidenceRecord], *, snapshot_id: str,
                              source_policies: Mapping[str, SourcePolicy], assessed_at: str,
                              decided_at: str, allow_warnings: bool = False,
                              conflict_record_ids: set[str] | None = None,
                              expected_record_ids: set[str] | None = None):
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
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT a13_assess_store_snapshot")
        conn.execute("RELEASE SAVEPOINT a13_assess_store_snapshot")
        raise
    return assessments, decision
