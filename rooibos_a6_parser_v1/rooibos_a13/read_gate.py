"""Fail-closed consumer gate for persisted A13 snapshot decisions.

Consumers must call this before reading research records. This module has no
network, credential, Moby or trading capability.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Iterable
from .models import EvidenceRecord, canonical_hash

class SnapshotAccessDenied(PermissionError):
    pass

@dataclass(frozen=True)
class GateGrant:
    snapshot_id: str
    dataset_type: str
    dataset_status: str
    snapshot_hash: str
    assessments_hash: str
    policy_version: str
    record_ids: tuple[str, ...]


def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt.astimezone(timezone.utc)


def snapshot_content_hash(records: Iterable[EvidenceRecord]) -> str:
    ordered = sorted(records, key=lambda r: r.record_id)
    return canonical_hash([
        {"record_id": r.record_id, "dataset_type": r.dataset_type,
         "chain": r.chain, "mint": r.mint, "source_id": r.source_id,
         "endpoint_id": r.endpoint_id, "run_id": r.run_id,
         "raw_response_id": r.raw_response_id, "source_record_index": r.source_record_index,
         "observed_at": r.observed_at, "normalization_version": r.normalization_version,
         "content_hash": r.content_hash}
        for r in ordered
    ])


def authorize_snapshot(conn: sqlite3.Connection, *, snapshot_id: str,
                       records: Iterable[EvidenceRecord], expected_snapshot_hash: str,
                       expected_assessments_hash: str, allowed_policy_versions: set[str],
                       allow_warnings: bool = False, now: str | None = None,
                       max_age_seconds: int | None = None,
                       require_lifecycle: bool = True, enforce_retention: bool = False) -> GateGrant:
    """Return a grant only when persisted governance and supplied evidence agree.

    `records` must be the exact records the caller intends to expose. Raw
    collector tables must not be exposed through a parallel ungated path.
    """
    rows = list(records)
    if not rows:
        raise SnapshotAccessDenied("empty snapshot")
    row = conn.execute(
        "SELECT dataset_type, record_ids_json, dataset_status, assessments_hash, policy_version, decided_at, snapshot_content_hash "
        "FROM a13_snapshot_decisions WHERE snapshot_id=?", (snapshot_id,)
    ).fetchone()
    if row is None:
        raise SnapshotAccessDenied("no persisted A13 decision for snapshot")
    dataset_type, ids_json, status, assessments_hash, policy_version, decided_at, persisted_snapshot_hash = row
    ids = tuple(json.loads(ids_json))
    supplied_ids = tuple(sorted(r.record_id for r in rows))
    if len(supplied_ids) != len(set(supplied_ids)) or tuple(sorted(ids)) != supplied_ids:
        raise SnapshotAccessDenied("record membership differs from approved snapshot")
    if any(r.dataset_type != dataset_type for r in rows):
        raise SnapshotAccessDenied("dataset type mismatch")
    if assessments_hash != expected_assessments_hash:
        raise SnapshotAccessDenied("assessment hash mismatch")
    if policy_version not in allowed_policy_versions:
        raise SnapshotAccessDenied("policy version is not allowed by consumer")
    if status != "approved_for_research" and not (status == "approved_with_warnings" and allow_warnings):
        raise SnapshotAccessDenied("snapshot status is not approved under consumer policy")
    actual_hash = snapshot_content_hash(rows)
    if actual_hash != expected_snapshot_hash:
        raise SnapshotAccessDenied("snapshot content hash mismatch")
    if not persisted_snapshot_hash or actual_hash != persisted_snapshot_hash:
        raise SnapshotAccessDenied("supplied evidence does not match the snapshot hash approved by A13")
    if max_age_seconds is not None:
        if max_age_seconds < 0 or now is None:
            raise SnapshotAccessDenied("freshness policy requires non-negative max_age_seconds and now")
        try:
            age = (_parse_time(now) - _parse_time(decided_at)).total_seconds()
        except (ValueError, AttributeError):
            raise SnapshotAccessDenied("invalid decision/current timestamp")
        if age < 0 or age > max_age_seconds:
            raise SnapshotAccessDenied("snapshot decision is stale or from the future")
    if require_lifecycle or enforce_retention:
        from .lifecycle import LifecycleError, lifecycle_state, assess_retention
        try:
            state = lifecycle_state(conn, snapshot_id)
        except (LifecycleError, sqlite3.Error) as exc:
            raise SnapshotAccessDenied(f"snapshot lifecycle unavailable or invalid: {exc}") from exc
        if state.state != "registered":
            raise SnapshotAccessDenied(f"snapshot lifecycle state is {state.state}, not registered")
        # Lifecycle registration is only valid when its immutable raw links
        # exactly represent the raw evidence referenced by the approved records.
        try:
            linked_raw_ids = {
                item[0] for item in conn.execute(
                    "SELECT raw_response_id FROM a13_snapshot_raw_links WHERE snapshot_id=?",
                    (snapshot_id,)
                ).fetchall()
            }
        except sqlite3.Error as exc:
            raise SnapshotAccessDenied(f"snapshot raw-evidence links unavailable: {exc}") from exc
        expected_raw_ids = {record.raw_response_id for record in rows}
        if None in expected_raw_ids or "" in expected_raw_ids or linked_raw_ids != expected_raw_ids:
            raise SnapshotAccessDenied("persisted raw-evidence links differ from approved snapshot records")
        if enforce_retention:
            try:
                retention = assess_retention(conn, snapshot_id=snapshot_id, now=now or "")
            except (LifecycleError, ValueError, sqlite3.Error) as exc:
                raise SnapshotAccessDenied(f"retention assessment failed: {exc}") from exc
            if retention.policy_id is None:
                raise SnapshotAccessDenied("no approved retention policy is assigned to this snapshot")
            if retention.expired:
                raise SnapshotAccessDenied("snapshot retention period has expired")
    return GateGrant(snapshot_id, dataset_type, status, actual_hash,
                     assessments_hash, policy_version, tuple(sorted(ids)))
