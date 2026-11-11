"""Immutable source-record membership receipts for publication recovery."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence


def _canonical_membership(source_record_count: int, membership: Mapping[int, Sequence[str]]) -> str:
    if isinstance(source_record_count, bool) or not isinstance(source_record_count, int) or source_record_count < 1:
        raise ValueError("source_record_count must be a positive integer")
    expected = set(range(source_record_count))
    if set(membership) != expected:
        raise ValueError("manifest must cover every original source-record index exactly once")
    normalized = {}
    seen_record_ids = set()
    for index in range(source_record_count):
        ids = list(membership[index])
        if any(not isinstance(value, str) or not value for value in ids):
            raise ValueError("manifest record IDs must be non-empty strings")
        if len(ids) != len(set(ids)) or seen_record_ids.intersection(ids):
            raise ValueError("manifest contains duplicate normalized record IDs across source indexes")
        seen_record_ids.update(ids)
        normalized[str(index)] = sorted(ids)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def store_record_manifest(
    conn: sqlite3.Connection, *, run_id: str, raw_response_id: str,
    collector_module: str, source_record_count: int,
    membership: Mapping[int, Sequence[str]],
) -> str:
    """Persist a one-time immutable receipt after canonical records are durable."""
    if collector_module not in {"A1", "A2", "A6"}:
        raise ValueError("unsupported collector module for source-record manifest")
    payload = _canonical_membership(source_record_count, membership)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        raw = conn.execute(
            "SELECT run_id FROM raw_responses WHERE raw_response_id=?",
            (raw_response_id,),
        ).fetchone()
        run = conn.execute(
            "SELECT collectors.module_code FROM collection_runs "
            "JOIN collectors ON collectors.collector_id=collection_runs.collector_id "
            "WHERE collection_runs.run_id=?",
            (run_id,),
        ).fetchone()
        if raw is None or raw["run_id"] != run_id:
            raise ValueError("manifest raw response does not belong to its collection run")
        if run is None or run["module_code"] != collector_module:
            raise ValueError("manifest collector module does not match the registered run")
        existing = conn.execute(
            "SELECT raw_response_id,collector_module,source_record_count,membership_json,manifest_sha256 "
            "FROM collection_record_manifests WHERE run_id=?",
            (run_id,),
        ).fetchone()
        values = (raw_response_id, collector_module, source_record_count, payload, digest)
        if existing:
            if tuple(existing) != values:
                raise ValueError("collection run already has a different immutable source-record manifest")
        else:
            conn.execute(
                "INSERT INTO collection_record_manifests "
                "(run_id,raw_response_id,collector_module,source_record_count,membership_json,manifest_sha256) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, *values),
            )
        if owns_transaction:
            conn.commit()
        return digest
    except Exception:
        if owns_transaction:
            conn.rollback()
        raise


def load_verified_manifest(conn: sqlite3.Connection, *, run_id: str, raw_response_id: str,
                           collector_module: str) -> dict[int, tuple[str, ...]]:
    row = conn.execute(
        "SELECT raw_response_id,collector_module,source_record_count,membership_json,manifest_sha256 "
        "FROM collection_record_manifests WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if row is None or (row["raw_response_id"], row["collector_module"]) != (
        raw_response_id, collector_module
    ):
        raise ValueError("publication recovery requires a matching immutable source-record manifest")
    digest = hashlib.sha256(row["membership_json"].encode("utf-8")).hexdigest()
    if digest != row["manifest_sha256"]:
        raise ValueError("source-record manifest hash verification failed")
    decoded = json.loads(row["membership_json"])
    expected = {str(i) for i in range(row["source_record_count"])}
    if set(decoded) != expected:
        raise ValueError("source-record manifest does not cover the original response")
    result = {}
    seen = set()
    for key, values in decoded.items():
        if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
            raise ValueError("source-record manifest membership is malformed")
        if len(values) != len(set(values)) or seen.intersection(values):
            raise ValueError("source-record manifest contains duplicate record IDs")
        seen.update(values)
        result[int(key)] = tuple(values)
    return result
