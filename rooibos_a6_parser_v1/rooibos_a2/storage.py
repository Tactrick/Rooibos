"""Append-only A2 observation persistence tied to immutable raw evidence."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
from typing import Iterable
from rooibos_db.database import _connect, initialize_database
from .normalizer import A2Result, NormalizedMetric


def persist_a2_result(db_path: str | Path, *, result: A2Result) -> dict:
    initialize_database(db_path)
    conn = _connect(db_path)
    inserted = replayed = conflicts = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for o in result.observations:
            raw = conn.execute("""SELECT run_id,source_id,endpoint_id FROM raw_responses WHERE raw_response_id=?""", (o.raw_response_id,)).fetchone()
            if not raw:
                raise ValueError(f"Unknown raw response: {o.raw_response_id}")
            if (raw["run_id"],raw["source_id"],raw["endpoint_id"]) != (o.run_id,o.source_id,o.endpoint_id):
                raise ValueError("A2 observation provenance does not match raw response")
            if o.market_id is not None:
                market = conn.execute("""SELECT m.source_id,a.chain,a.token_address
                    FROM markets m JOIN assets a ON a.asset_id=m.asset_id WHERE m.market_id=?""", (o.market_id,)).fetchone()
                if not market or (market["source_id"],market["chain"],market["token_address"]) != (o.source_id,o.chain,o.mint_address):
                    raise ValueError("Market identity does not match observation source and asset")
            run = conn.execute("SELECT status FROM collection_runs WHERE run_id=?",(o.run_id,)).fetchone()
            if not run or run["status"] not in {"raw_captured","parsed","normalised","validating","partial"}:
                raise ValueError("A2 persistence requires a captured/parsed collection run")
            existing = conn.execute(
                """SELECT chain,mint_address,market_id,pool_address,market_scope,metric_code,
                          numeric_value,text_value,value_status,unit,quote_currency,window_seconds,
                          measurement_kind,observed_at,source_id,endpoint_id,run_id,raw_response_id,
                          source_record_index,normalization_version,metadata_json,content_sha256
                   FROM market_observations WHERE observation_id=?""",
                (o.observation_id,),
            ).fetchone()
            values = (
                o.chain,o.mint_address,o.market_id,o.pool_address,o.market_scope,o.metric_code,
                o.numeric_value,o.text_value,o.value_status,o.unit,o.quote_currency,o.window_seconds,
                o.measurement_kind,o.observed_at,o.source_id,o.endpoint_id,o.run_id,o.raw_response_id,
                o.source_record_index,o.normalization_version,o.metadata_json,o.content_sha256,
            )
            if existing:
                if tuple(existing) != values:
                    raise ValueError(
                        "Observation ID collision with different content or provenance"
                    )
                replayed += 1
                continue
            conn.execute("""INSERT INTO market_observations(
                observation_id,chain,mint_address,market_id,pool_address,market_scope,metric_code,
                numeric_value,text_value,value_status,unit,quote_currency,window_seconds,measurement_kind,
                observed_at,source_id,endpoint_id,run_id,raw_response_id,source_record_index,
                normalization_version,metadata_json,content_sha256
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                o.observation_id,o.chain,o.mint_address,o.market_id,o.pool_address,o.market_scope,o.metric_code,
                o.numeric_value,o.text_value,o.value_status,o.unit,o.quote_currency,o.window_seconds,o.measurement_kind,
                o.observed_at,o.source_id,o.endpoint_id,o.run_id,o.raw_response_id,o.source_record_index,
                o.normalization_version,o.metadata_json,o.content_sha256
            ))
            inserted += 1
        conn.commit()
        return {"inserted_count":inserted,"idempotent_replay_count":replayed,"storage_conflict_count":conflicts,"status":"persisted_unvalidated"}
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def persist_comparison(db_path: str | Path, *, comparison, observations: list[NormalizedMetric], tolerance_pct: float) -> str:
    """Append a comparison result without changing or selecting observations."""
    import hashlib
    initialize_database(db_path)
    if not observations:
        raise ValueError("At least one observation is required")
    first = observations[0]
    ids = sorted(o.observation_id for o in observations)
    conn_check = _connect(db_path)
    try:
        present = {row[0] for row in conn_check.execute(
            f"SELECT observation_id FROM market_observations WHERE observation_id IN ({','.join(['?']*len(ids))})", ids
        )}
        if present != set(ids):
            raise ValueError("Comparison can only reference observations already persisted")
    finally:
        conn_check.close()
    scope = {
        "market_scope": first.market_scope, "market_id": first.market_id,
        "pool_address": first.pool_address, "unit": first.unit,
        "quote_currency": first.quote_currency, "window_seconds": first.window_seconds,
    }
    canonical = json.dumps({"ids":ids,"scope":scope,"outcome":comparison.outcome,"tolerance_pct":tolerance_pct},sort_keys=True,separators=(",",":"))
    comparison_id = hashlib.sha256(canonical.encode()).hexdigest()[:32]
    conn = _connect(db_path)
    try:
        conn.execute("""INSERT OR IGNORE INTO market_observation_comparisons(
            comparison_id,chain,mint_address,metric_code,outcome,observation_ids_json,
            comparison_scope_json,tolerance_pct,minimum_value,maximum_value,spread_pct,reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
            comparison_id,first.chain,first.mint_address,first.metric_code,comparison.outcome,
            json.dumps(ids,separators=(",",":")),json.dumps(scope,sort_keys=True,separators=(",",":")),
            tolerance_pct,comparison.minimum_value,comparison.maximum_value,comparison.spread_pct,comparison.reason
        ))
        conn.commit()
        return comparison_id
    finally:
        conn.close()
