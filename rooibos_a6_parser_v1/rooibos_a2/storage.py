"""Append-only A2 observation persistence tied to immutable raw evidence."""
from __future__ import annotations
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable
from rooibos_db.database import _connect, initialize_database
from .normalizer import A2Result, NormalizedMetric
from .comparison import ComparisonResult, compare_observations


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
                    raise ValueError("Observation ID collision with different content or provenance")
                replayed += 1
                continue
            if run["status"] == "validating":
                raise ValueError("A2 cannot append new observations after validation has begun")
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


def _metric_from_row(row: sqlite3.Row) -> NormalizedMetric:
    """Rehydrate the exact immutable observation representation used by A2."""
    return NormalizedMetric(
        observation_id=row["observation_id"],
        chain=row["chain"],
        mint_address=row["mint_address"],
        market_id=row["market_id"],
        pool_address=row["pool_address"],
        market_scope=row["market_scope"],
        metric_code=row["metric_code"],
        numeric_value=row["numeric_value"],
        text_value=row["text_value"],
        value_status=row["value_status"],
        unit=row["unit"],
        quote_currency=row["quote_currency"],
        window_seconds=row["window_seconds"],
        measurement_kind=row["measurement_kind"],
        observed_at=row["observed_at"],
        source_id=row["source_id"],
        endpoint_id=row["endpoint_id"],
        run_id=row["run_id"],
        raw_response_id=row["raw_response_id"],
        source_record_index=row["source_record_index"],
        normalization_version=row["normalization_version"],
        metadata_json=row["metadata_json"],
        content_sha256=row["content_sha256"],
    )


def persist_comparison(db_path: str | Path, *, comparison: ComparisonResult,
                       observations: list[NormalizedMetric], tolerance_pct: float) -> str:
    """Persist only a comparison independently reproduced from stored observations.

    The supplied observations contribute identifiers only. Their values and
    provenance are rehydrated under the same write transaction that inserts
    the comparison, eliminating caller-controlled values and the prior
    check/insert race.
    """
    import hashlib

    if not observations:
        raise ValueError("At least one observation is required")
    if isinstance(tolerance_pct, bool) or not isinstance(tolerance_pct, (int, float)) or not math.isfinite(float(tolerance_pct)) or tolerance_pct < 0:
        raise ValueError("tolerance_pct must be a finite non-negative number")
    ids_in_order = [o.observation_id for o in observations]
    if any(not isinstance(value, str) or not value for value in ids_in_order):
        raise ValueError("Every comparison observation must have an ID")
    if len(set(ids_in_order)) != len(ids_in_order):
        raise ValueError("Comparison observation IDs must be unique")

    initialize_database(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in ids_in_order)
        rows = conn.execute(
            f"SELECT * FROM market_observations WHERE observation_id IN ({placeholders})",
            ids_in_order,
        ).fetchall()
        by_id = {row["observation_id"]: row for row in rows}
        if set(by_id) != set(ids_in_order):
            raise ValueError("Comparison can only reference observations already persisted")
        persisted = [_metric_from_row(by_id[observation_id]) for observation_id in ids_in_order]
        reproduced = compare_observations(persisted, tolerance_pct=float(tolerance_pct))
        if not isinstance(comparison, ComparisonResult) or comparison != reproduced:
            raise ValueError("Supplied comparison does not match persisted observations")

        first = persisted[0]
        scope = {
            "market_scope": first.market_scope,
            "market_id": first.market_id,
            "pool_address": first.pool_address,
            "unit": first.unit,
            "quote_currency": first.quote_currency,
            "window_seconds": first.window_seconds,
        }
        canonical_ids = sorted(ids_in_order)
        canonical = json.dumps(
            {"ids": canonical_ids, "scope": scope, "outcome": reproduced.outcome,
             "tolerance_pct": float(tolerance_pct)},
            sort_keys=True, separators=(",", ":"),
        )
        comparison_id = hashlib.sha256(canonical.encode()).hexdigest()[:32]
        conn.execute("""INSERT OR IGNORE INTO market_observation_comparisons(
            comparison_id,chain,mint_address,metric_code,outcome,observation_ids_json,
            comparison_scope_json,tolerance_pct,minimum_value,maximum_value,spread_pct,reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (
            comparison_id,first.chain,first.mint_address,first.metric_code,reproduced.outcome,
            json.dumps(canonical_ids,separators=(",",":")),
            json.dumps(scope,sort_keys=True,separators=(",",":")),
            float(tolerance_pct),reproduced.minimum_value,reproduced.maximum_value,
            reproduced.spread_pct,reproduced.reason,
        ))
        conn.commit()
        return comparison_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
