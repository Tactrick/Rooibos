"""Controlled SQLite persistence for A6 parser outputs.

This module stores immutable raw observations, parser manifests and
unvalidated canonical OHLCV candidates. It does not approve research data.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
import uuid

from rooibos_db.database import _connect, initialize_database
from .parser import ParseResult


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def store_raw_response(
    db_path: str | Path,
    *, raw_response_id: str, run_id: str, source_id: str,
    endpoint_id: str | None, payload: Any, request_params: Mapping[str, Any],
    request_id: str | None = None,
    retrieved_at: str | None = None, provider_timestamp: str | None = None,
    http_status: int | None = None, response_headers: Mapping[str, Any] | None = None,
    content_type: str | None = "application/json", page_sequence: int | None = None,
) -> str:
    """Persist raw JSON before parsing. Existing raw IDs are never overwritten."""
    initialize_database(db_path)
    payload_json = _json(payload)
    payload_hash = _sha256(payload_json)
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        request_json = _json(request_params)
        headers_json = _json(response_headers) if response_headers is not None else None
        existing = conn.execute(
            "SELECT run_id,request_id,source_id,endpoint_id,payload_sha256,request_params_json,response_headers_json,"
            "provider_timestamp,retrieved_at,http_status,content_type,page_sequence FROM raw_responses WHERE raw_response_id=?",
            (raw_response_id,),
        ).fetchone()
        if existing:
            supplied = (run_id,request_id,source_id,endpoint_id,payload_hash,request_json,headers_json,
                        provider_timestamp,retrieved_at,http_status,content_type,page_sequence)
            stored = tuple(existing[k] for k in ("run_id","request_id","source_id","endpoint_id","payload_sha256",
                        "request_params_json","response_headers_json","provider_timestamp","retrieved_at",
                        "http_status","content_type","page_sequence"))
            if stored != supplied:
                raise ValueError("raw_response_id already exists with different evidence or metadata; raw evidence is immutable")
            conn.commit()
            return payload_hash
        conn.execute("""
            INSERT INTO raw_responses(
                raw_response_id,run_id,request_id,source_id,endpoint_id,provider_timestamp,retrieved_at,
                http_status,request_params_json,response_headers_json,payload_json,payload_sha256,
                content_type,page_sequence
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            raw_response_id, run_id, request_id, source_id, endpoint_id, provider_timestamp, retrieved_at,
            http_status, request_json, headers_json,
            payload_json, payload_hash, content_type, page_sequence,
        ))
        conn.commit()
        return payload_hash
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def persist_parse_result(
    db_path: str | Path,
    *, result: ParseResult, raw_response_id: str, normalization_event_id: str | None = None,
) -> dict[str, Any]:
    """Persist parser evidence and non-approved candles in one transaction.

    Conflicting variants remain in the normalization event manifest, rather
    than being forced into the canonical table's unique candle identity.
    Replays of an identical candle are idempotent. Existing differing values
    are preserved and reported as a storage conflict; they are never replaced.
    """
    initialize_database(db_path)
    event_id = normalization_event_id or str(uuid.uuid4())
    manifest = result.manifest()
    manifest["raw_response_id"] = raw_response_id
    manifest["candles"] = [asdict(c) for c in result.candles]
    inserted = 0
    existing_identical = 0
    storage_conflicts = 0
    conflicted_identity_keys: set[tuple[Any, ...]] = set()
    # Parser conflict findings are the authority for not inserting any variant.
    parser_conflict_indices = {
        f.source_record_index for f in result.findings if f.code == "CONFLICTING_CANDLE"
    }
    parser_conflicted_timestamps = {
        c.open_time_ms for c in result.candles if c.source_record_index in parser_conflict_indices
    }
    # A6's provider record index identifies one source record in one immutable
    # response. It must not be reused to manufacture multiple candle records.
    source_indices = [c.source_record_index for c in result.candles]
    if any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in source_indices):
        raise ValueError("Every A6 candle requires a non-negative integer source_record_index")
    if len(set(source_indices)) != len(source_indices):
        raise ValueError("A6 source_record_index must be unique within a raw response")

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        raw = conn.execute(
            "SELECT rr.run_id,rr.source_id,rr.endpoint_id,cr.status AS run_status "
            "FROM raw_responses rr JOIN collection_runs cr ON cr.run_id=rr.run_id "
            "WHERE rr.raw_response_id=?",
            (raw_response_id,),
        ).fetchone()
        if not raw:
            raise ValueError("Raw response must be stored before parser output")
        if raw["run_status"] not in ("parsed", "partial", "normalised"):
            raise ValueError("Collection run must enter parsed state before canonical persistence")
        for candle in result.candles:
            if candle.raw_response_ref != raw_response_id:
                raise ValueError("Candle raw_response_ref does not match persisted raw response")
            if (candle.collection_run_id != raw["run_id"] or candle.source_id != raw["source_id"]
                    or candle.endpoint_id != raw["endpoint_id"]):
                raise ValueError("Candle provenance does not match raw response run/source/endpoint")
            if candle.open_time_ms in parser_conflicted_timestamps:
                continue
            # Validate asset and market identity before either the insert or
            # identical-candle replay path. Previously the latter could attach
            # evidence while bypassing these checks.
            asset = conn.execute(
                "SELECT asset_id FROM assets WHERE chain=? AND token_address=?",
                (candle.chain,candle.mint_address),
            ).fetchone()
            if not asset:
                raise ValueError(f"Asset is not registered: {candle.chain}/{candle.mint_address}")
            if asset["asset_id"] != candle.asset_id:
                raise ValueError("ParseContext asset_id does not match the registered chain/mint identity")
            if candle.market_id is not None and not conn.execute(
                "SELECT 1 FROM markets WHERE market_id=? AND asset_id=?",
                (candle.market_id,asset["asset_id"]),
            ).fetchone():
                raise ValueError("Market identity is not registered for this asset")
            identity = (
                candle.chain, candle.mint_address, candle.market_id, candle.interval,
                candle.open_time_ms, candle.source_id, candle.normalisation_version,
            )
            existing = conn.execute("""
                SELECT c.candle_id,c.open,c.high,c.low,c.close,c.volume,c.candle_status,c.volume_unit
                FROM ohlcv_candles c JOIN assets a ON a.asset_id=c.asset_id
                WHERE a.chain=? AND a.token_address=? AND c.market_id IS ? AND c.interval=?
                  AND c.open_time_ms=? AND c.source_id=? AND c.normalization_version=?
            """, identity).fetchone()
            values = (candle.open,candle.high,candle.low,candle.close,candle.volume,candle.candle_status,candle.volume_unit)
            if existing:
                old = tuple(existing[k] for k in ("open","high","low","close","volume","candle_status","volume_unit"))
                if old == values:
                    # The same source-record index cannot support a different
                    # canonical candle within this immutable raw response.
                    reused = conn.execute(
                        "SELECT candle_id FROM a6_candle_evidence_links "
                        "WHERE raw_response_id=? AND source_record_index=? AND candle_id<>?",
                        (raw_response_id,candle.source_record_index,existing["candle_id"]),
                    ).fetchone()
                    if reused:
                        raise ValueError("A6 source_record_index is already linked to a different candle in this raw response")
                    # Preserve every raw observation that independently confirms this
                    # canonical candle. The candle row remains deduplicated.
                    conn.execute("""
                        INSERT OR IGNORE INTO a6_candle_evidence_links(
                            candle_id,raw_response_id,run_id,source_id,endpoint_id,source_record_index
                        ) VALUES (?,?,?,?,?,?)
                    """, (
                        existing["candle_id"], raw_response_id, raw["run_id"], raw["source_id"],
                        raw["endpoint_id"], candle.source_record_index,
                    ))
                    existing_identical += 1
                else:
                    storage_conflicts += 1
                    conflicted_identity_keys.add(identity)
                continue
            candle_id = _sha256(_json(identity))
            reused = conn.execute(
                "SELECT candle_id FROM a6_candle_evidence_links "
                "WHERE raw_response_id=? AND source_record_index=? AND candle_id<>?",
                (raw_response_id,candle.source_record_index,candle_id),
            ).fetchone()
            if reused:
                raise ValueError("A6 source_record_index is already linked to a different candle in this raw response")
            conn.execute("""
                INSERT INTO ohlcv_candles(
                    candle_id,asset_id,market_id,interval,open_time_ms,source_id,normalization_version,
                    open,high,low,close,volume,raw_response_id,run_id,quality_status,
                    candle_status,volume_unit,source_record_index,endpoint_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                candle_id,asset["asset_id"],candle.market_id,candle.interval,candle.open_time_ms,
                candle.source_id,candle.normalisation_version,candle.open,candle.high,candle.low,
                candle.close,candle.volume,raw_response_id,candle.collection_run_id,"unvalidated",
                candle.candle_status,candle.volume_unit,candle.source_record_index,candle.endpoint_id,
            ))
            conn.execute("""
                INSERT OR IGNORE INTO a6_candle_evidence_links(
                    candle_id,raw_response_id,run_id,source_id,endpoint_id,source_record_index
                ) VALUES (?,?,?,?,?,?)
            """, (
                candle_id, raw_response_id, raw["run_id"], raw["source_id"],
                raw["endpoint_id"], candle.source_record_index,
            ))
            inserted += 1

        manifest["storage"] = {
            "inserted_count": inserted,
            "existing_identical_count": existing_identical,
            "storage_conflict_count": storage_conflicts,
            "parser_conflict_variant_count": len(parser_conflicted_timestamps),
            "quality_status": "unvalidated",
        }
        conn.execute("""
            INSERT INTO normalization_events(
                normalization_event_id,raw_response_id,run_id,parser_name,parser_version,
                input_record_count,output_record_count,rejected_record_count,status,details_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            event_id,raw_response_id,raw["run_id"],manifest["parser_name"],manifest["parser_version"],
            result.received_count,inserted+existing_identical,result.rejected_count,
            "completed_with_findings" if (result.findings or storage_conflicts) else "completed",
            _json(manifest),
        ))
        conn.execute("""
            UPDATE collection_runs SET status='normalised',records_received=?,records_parsed=?,
                records_rejected=?,response_count=MAX(response_count,1),error_summary=?
            WHERE run_id=?
        """, (
            result.received_count,result.decoded_count,result.rejected_count,
            f"A6 parser findings={len(result.findings)}; storage conflicts={storage_conflicts}" if result.findings or storage_conflicts else None,
            raw["run_id"],
        ))
        conn.commit()
        return {
            "normalization_event_id": event_id,
            "inserted_count": inserted,
            "existing_identical_count": existing_identical,
            "storage_conflict_count": storage_conflicts,
            "parser_conflict_variant_count": len(parser_conflicted_timestamps),
            "status": "persisted_unvalidated",
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
