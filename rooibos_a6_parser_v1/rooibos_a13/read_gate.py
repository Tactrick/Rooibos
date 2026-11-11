"""Fail-closed consumer gate for persisted A13 snapshot decisions.

Consumers must call this before reading research records. This module has no
network, credential, Moby or trading capability.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Iterable, Mapping
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
    # Revalidate the payload itself at read time. The snapshot hash binds the
    # stored content_hash, not a mutable Mapping supplied by the caller.
    for record in rows:
        if not isinstance(record.payload, Mapping) or canonical_hash(record.payload) != record.content_hash:
            raise SnapshotAccessDenied(
                "evidence payload hash does not match its A13 record content hash"
            )
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
    # Exact record-to-raw-source coordinates are a core provenance check,
    # independent of whether the consumer separately requires lifecycle state.
    expected_evidence_links = set()
    for record in rows:
        index = record.source_record_index
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise SnapshotAccessDenied("approved record has an invalid original source-record index")
        expected_evidence_links.add((record.record_id, record.raw_response_id, index))
    try:
        persisted_evidence_links = {
            (item[0], item[1], item[2])
            for item in conn.execute(
                "SELECT record_id, raw_response_id, source_record_index "
                "FROM a13_snapshot_evidence_links WHERE snapshot_id=?",
                (snapshot_id,)
            ).fetchall()
        }
    except sqlite3.Error as exc:
        raise SnapshotAccessDenied(f"snapshot source-coordinate links unavailable: {exc}") from exc
    if persisted_evidence_links != expected_evidence_links:
        raise SnapshotAccessDenied(
            "persisted record-to-raw-response/source-index links differ from approved evidence"
        )
    # Do not trust the link table alone: independently bind each approved
    # record's run/source/endpoint identity to the immutable raw response row.
    try:
        for record in rows:
            raw = conn.execute(
                """SELECT rr.run_id, rr.source_id, rr.endpoint_id,
                          cr.source_id AS run_source_id, cr.endpoint_id AS run_endpoint_id
                   FROM raw_responses rr
                   JOIN collection_runs cr ON cr.run_id=rr.run_id
                   WHERE rr.raw_response_id=?""",
                (record.raw_response_id,),
            ).fetchone()
            if raw is None or (
                raw["run_id"], raw["source_id"], raw["endpoint_id"],
                raw["run_source_id"], raw["run_endpoint_id"],
            ) != (
                record.run_id, record.source_id, record.endpoint_id,
                record.source_id, record.endpoint_id,
            ):
                raise SnapshotAccessDenied(
                    "record run/source/endpoint provenance differs from its persisted raw response"
                )
    except sqlite3.Error as exc:
        raise SnapshotAccessDenied(f"raw-response provenance unavailable: {exc}") from exc

    if require_lifecycle or enforce_retention:
        from .lifecycle import LifecycleError, lifecycle_state, assess_retention
        try:
            state = lifecycle_state(conn, snapshot_id)
        except (LifecycleError, sqlite3.Error) as exc:
            raise SnapshotAccessDenied(f"snapshot lifecycle unavailable or invalid: {exc}") from exc
        if state.state != "registered":
            raise SnapshotAccessDenied(f"snapshot lifecycle state is {state.state}, not registered")
        # Snapshot-level raw links remain a compact index for retention.
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
