"""Append-only A13 snapshot lifecycle and retention governance.

Lifecycle events never mutate evidence or prior A13 decisions. Retention is
computed and reported; this module deliberately performs no deletion.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from typing import Iterable

from .models import canonical_hash

class LifecycleError(ValueError):
    pass

@dataclass(frozen=True)
class LifecycleState:
    snapshot_id: str
    state: str
    latest_event_id: str
    latest_event_at: str
    superseded_by: str | None = None
    withdrawn: bool = False
    legal_hold: bool = False

@dataclass(frozen=True)
class RetentionAssessment:
    snapshot_id: str
    policy_id: str | None
    retain_until: str | None
    expired: bool
    legal_hold: bool
    raw_response_ids: tuple[str, ...]
    deletion_permitted: bool = False


def _dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise LifecycleError("timestamps must include timezone")
    return dt.astimezone(timezone.utc)

def _mapping(cursor, row):
    if row is None: return None
    if hasattr(row, "keys"): return dict(row)
    return dict(zip([column[0] for column in cursor.description], row))


def apply_migration_008(conn: sqlite3.Connection) -> None:
    conn.executescript(r'''
    CREATE TABLE IF NOT EXISTS a13_snapshot_lifecycle_events (
      event_id TEXT PRIMARY KEY,
      snapshot_id TEXT NOT NULL REFERENCES a13_snapshot_decisions(snapshot_id),
      sequence_no INTEGER NOT NULL,
      event_type TEXT NOT NULL CHECK(event_type IN ('registered','superseded','withdrawn','legal_hold_placed','legal_hold_released')),
      effective_at TEXT NOT NULL,
      actor_id TEXT NOT NULL,
      reason TEXT NOT NULL,
      related_snapshot_id TEXT REFERENCES a13_snapshot_decisions(snapshot_id),
      metadata_json TEXT NOT NULL DEFAULT '{}',
      previous_event_hash TEXT NOT NULL,
      event_hash TEXT NOT NULL UNIQUE,
      UNIQUE(snapshot_id, sequence_no)
    );
    CREATE INDEX IF NOT EXISTS idx_a13_lifecycle_snapshot_sequence
      ON a13_snapshot_lifecycle_events(snapshot_id, sequence_no);
    CREATE TABLE IF NOT EXISTS a13_retention_policies (
      policy_id TEXT NOT NULL,
      version INTEGER NOT NULL,
      dataset_type TEXT NOT NULL,
      retention_days INTEGER NOT NULL CHECK(retention_days >= 1),
      effective_at TEXT NOT NULL,
      approval_reference TEXT NOT NULL,
      created_by TEXT NOT NULL,
      policy_hash TEXT NOT NULL UNIQUE,
      PRIMARY KEY(policy_id, version)
    );
    CREATE TABLE IF NOT EXISTS a13_snapshot_retention_assignments (
      snapshot_id TEXT PRIMARY KEY REFERENCES a13_snapshot_decisions(snapshot_id),
      policy_id TEXT NOT NULL,
      policy_version INTEGER NOT NULL,
      assigned_at TEXT NOT NULL,
      assigned_by TEXT NOT NULL,
      FOREIGN KEY(policy_id, policy_version) REFERENCES a13_retention_policies(policy_id, version)
    );
    CREATE TABLE IF NOT EXISTS a13_snapshot_raw_links (
      snapshot_id TEXT NOT NULL REFERENCES a13_snapshot_decisions(snapshot_id),
      raw_response_id TEXT NOT NULL REFERENCES raw_responses(raw_response_id),
      linked_at TEXT NOT NULL,
      PRIMARY KEY(snapshot_id, raw_response_id)
    );
    CREATE TRIGGER IF NOT EXISTS trg_a13_lifecycle_no_update BEFORE UPDATE ON a13_snapshot_lifecycle_events BEGIN SELECT RAISE(ABORT,'a13_snapshot_lifecycle_events is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_a13_lifecycle_no_delete BEFORE DELETE ON a13_snapshot_lifecycle_events BEGIN SELECT RAISE(ABORT,'a13_snapshot_lifecycle_events is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_a13_retention_policy_no_update BEFORE UPDATE ON a13_retention_policies BEGIN SELECT RAISE(ABORT,'a13_retention_policies is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_a13_retention_policy_no_delete BEFORE DELETE ON a13_retention_policies BEGIN SELECT RAISE(ABORT,'a13_retention_policies is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_a13_retention_assignment_no_update BEFORE UPDATE ON a13_snapshot_retention_assignments BEGIN SELECT RAISE(ABORT,'a13_snapshot_retention_assignments is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_a13_retention_assignment_no_delete BEFORE DELETE ON a13_snapshot_retention_assignments BEGIN SELECT RAISE(ABORT,'a13_snapshot_retention_assignments is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_a13_raw_links_no_update BEFORE UPDATE ON a13_snapshot_raw_links BEGIN SELECT RAISE(ABORT,'a13_snapshot_raw_links is append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_a13_raw_links_no_delete BEFORE DELETE ON a13_snapshot_raw_links BEGIN SELECT RAISE(ABORT,'a13_snapshot_raw_links is append-only'); END;
    ''')
    conn.commit()


def _append_event(conn: sqlite3.Connection, *, event_id: str, snapshot_id: str,
                  event_type: str, effective_at: str, actor_id: str, reason: str,
                  related_snapshot_id: str | None = None, metadata: dict | None = None) -> None:
    if not all((event_id, snapshot_id, actor_id, reason)):
        raise LifecycleError("event_id, snapshot_id, actor_id and reason are required")
    _dt(effective_at)
    latest_cursor = conn.execute("SELECT sequence_no,event_hash,event_type,effective_at FROM a13_snapshot_lifecycle_events WHERE snapshot_id=? ORDER BY sequence_no DESC LIMIT 1", (snapshot_id,))
    latest = _mapping(latest_cursor, latest_cursor.fetchone())
    if latest:
        if _dt(effective_at) < _dt(latest["effective_at"]):
            raise LifecycleError("lifecycle event time cannot move backwards")
        prior_state = latest["event_type"]
        seq, previous_hash = latest["sequence_no"] + 1, latest["event_hash"]
    else:
        seq, previous_hash, prior_state = 1, "0" * 64, None
    allowed = {
      None: {"registered"},
      "registered": {"superseded", "withdrawn", "legal_hold_placed"},
      "legal_hold_placed": {"legal_hold_released", "withdrawn", "superseded"},
      "legal_hold_released": {"legal_hold_placed", "withdrawn", "superseded"},
      "superseded": set(), "withdrawn": set(),
    }
    if event_type not in allowed.get(prior_state, set()):
        raise LifecycleError(f"invalid lifecycle transition: {prior_state!r} -> {event_type!r}")
    body = {"event_id":event_id,"snapshot_id":snapshot_id,"sequence_no":seq,"event_type":event_type,
            "effective_at":effective_at,"actor_id":actor_id,"reason":reason,
            "related_snapshot_id":related_snapshot_id,"metadata":metadata or {},"previous_event_hash":previous_hash}
    digest = canonical_hash(body)
    conn.execute("INSERT INTO a13_snapshot_lifecycle_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
       (event_id,snapshot_id,seq,event_type,effective_at,actor_id,reason,related_snapshot_id,
        json.dumps(metadata or {},sort_keys=True),previous_hash,digest))


def register_snapshot(conn: sqlite3.Connection, *, snapshot_id: str, event_id: str,
                      effective_at: str, actor_id: str, reason: str,
                      raw_response_ids: Iterable[str] = (), commit: bool = True) -> None:
    decision = conn.execute("SELECT dataset_status FROM a13_snapshot_decisions WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    if decision is None or decision[0] not in ("approved_for_research", "approved_with_warnings"):
        raise LifecycleError("only a persisted A13-approved snapshot can be registered")
    raw_ids = sorted(set(raw_response_ids))
    if not raw_ids:
        raise LifecycleError("at least one linked raw response is required to publish a snapshot")
    owns_transaction = not conn.in_transaction
    if commit and owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        _append_event(conn,event_id=event_id,snapshot_id=snapshot_id,event_type="registered",effective_at=effective_at,
                      actor_id=actor_id,reason=reason)
        for raw_id in raw_ids:
            if not raw_id: raise LifecycleError("raw response IDs cannot be empty")
            if conn.execute("SELECT 1 FROM raw_responses WHERE raw_response_id=?", (raw_id,)).fetchone() is None:
                raise LifecycleError(f"raw response {raw_id!r} does not exist")
            conn.execute("INSERT INTO a13_snapshot_raw_links VALUES (?,?,?)",(snapshot_id,raw_id,effective_at))
        if commit and owns_transaction:
            conn.commit()
    except Exception:
        if commit and owns_transaction:
            conn.rollback()
        raise



def register_snapshot_for_records(conn: sqlite3.Connection, *, snapshot_id: str, event_id: str,
                                  effective_at: str, actor_id: str, reason: str, records,
                                  commit: bool = True) -> None:
    decision = conn.execute(
        "SELECT dataset_type, record_ids_json, dataset_status, snapshot_content_hash "
        "FROM a13_snapshot_decisions WHERE snapshot_id=?", (snapshot_id,)
    ).fetchone()
    if decision is None:
        raise LifecycleError("snapshot decision does not exist")
    dataset_type, record_ids_json, status, approved_hash = decision
    if status not in ("approved_for_research", "approved_with_warnings"):
        raise LifecycleError("only an approved snapshot can be published")
    records = list(records)
    expected = set(json.loads(record_ids_json))
    supplied = {record.record_id for record in records}
    if expected != supplied or not supplied or len(supplied) != len(records):
        raise LifecycleError("records supplied for publication do not match approved snapshot membership")
    if any(record.dataset_type != dataset_type for record in records):
        raise LifecycleError("record dataset type differs from approved snapshot")
    # Import locally to avoid a module-level lifecycle/read-gate import cycle.
    from .read_gate import snapshot_content_hash
    if not approved_hash or snapshot_content_hash(records) != approved_hash:
        raise LifecycleError("record content or raw-evidence provenance differs from approved snapshot")
    raw_ids = {record.raw_response_id for record in records}
    if None in raw_ids or "" in raw_ids:
        raise LifecycleError("every approved record must reference a raw response")
    for record in records:
        index = record.source_record_index
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise LifecycleError("every approved record must reference a valid original source-record index")
        raw = conn.execute(
            "SELECT run_id, source_id, endpoint_id FROM raw_responses WHERE raw_response_id=?",
            (record.raw_response_id,),
        ).fetchone()
        if raw is None or (
            raw["run_id"], raw["source_id"], raw["endpoint_id"]
        ) != (record.run_id, record.source_id, record.endpoint_id):
            raise LifecycleError(
                "record provenance does not match its persisted raw response run/source/endpoint"
            )
    owns_transaction = not conn.in_transaction
    conn.execute("SAVEPOINT a13_register_snapshot_for_records")
    try:
        register_snapshot(conn, snapshot_id=snapshot_id, event_id=event_id,
                          effective_at=effective_at, actor_id=actor_id, reason=reason,
                          raw_response_ids=raw_ids, commit=False)
        for record in records:
            conn.execute(
                "INSERT INTO a13_snapshot_evidence_links "
                "(snapshot_id, record_id, raw_response_id, source_record_index, linked_at) "
                "VALUES (?,?,?,?,?)",
                (snapshot_id, record.record_id, record.raw_response_id,
                 record.source_record_index, effective_at),
            )
        conn.execute("RELEASE SAVEPOINT a13_register_snapshot_for_records")
        if commit and owns_transaction:
            conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT a13_register_snapshot_for_records")
        conn.execute("RELEASE SAVEPOINT a13_register_snapshot_for_records")
        if commit and owns_transaction:
            conn.rollback()
        raise


def supersede_snapshot(conn: sqlite3.Connection, *, old_snapshot_id: str, new_snapshot_id: str,
                       old_event_id: str, new_event_id: str, effective_at: str,
                       actor_id: str, reason: str, new_raw_response_ids: Iterable[str]) -> None:
    """Reject legacy raw-only supersession.

    A list of raw response IDs cannot prove which original source record
    produced each replacement record. Use supersede_snapshot_for_records()
    so the replacement lifecycle event and exact provenance links are atomic.
    """
    raise LifecycleError(
        "raw-response-only supersession is not publication-safe; "
        "use supersede_snapshot_for_records with approved evidence records"
    )

def withdraw_snapshot(conn: sqlite3.Connection, *, snapshot_id: str, event_id: str,
                      effective_at: str, actor_id: str, reason: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        _append_event(conn,event_id=event_id,snapshot_id=snapshot_id,event_type="withdrawn",effective_at=effective_at,
                      actor_id=actor_id,reason=reason)
        conn.commit()
    except Exception:
        conn.rollback(); raise


def set_legal_hold(conn: sqlite3.Connection, *, snapshot_id: str, event_id: str,
                   effective_at: str, actor_id: str, reason: str, held: bool) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        _append_event(conn,event_id=event_id,snapshot_id=snapshot_id,
          event_type="legal_hold_placed" if held else "legal_hold_released",effective_at=effective_at,
          actor_id=actor_id,reason=reason)
        conn.commit()
    except Exception:
        conn.rollback(); raise


def lifecycle_state(conn: sqlite3.Connection, snapshot_id: str) -> LifecycleState:
    cursor = conn.execute("SELECT * FROM a13_snapshot_lifecycle_events WHERE snapshot_id=? ORDER BY sequence_no",(snapshot_id,))
    raw_rows = cursor.fetchall()
    if not raw_rows: raise LifecycleError("snapshot has no lifecycle registration")
    names = [item[0] for item in cursor.description]
    rows = [dict(row) if hasattr(row, "keys") else dict(zip(names, row)) for row in raw_rows]
    # Verify the complete hash chain on every security-sensitive read.
    previous = "0" * 64
    for row in rows:
        body = {"event_id":row["event_id"],"snapshot_id":row["snapshot_id"],"sequence_no":row["sequence_no"],
          "event_type":row["event_type"],"effective_at":row["effective_at"],"actor_id":row["actor_id"],
          "reason":row["reason"],"related_snapshot_id":row["related_snapshot_id"],
          "metadata":json.loads(row["metadata_json"]),"previous_event_hash":previous}
        if row["previous_event_hash"] != previous or canonical_hash(body) != row["event_hash"]:
            raise LifecycleError("lifecycle event hash chain is invalid")
        previous = row["event_hash"]
    latest = rows[-1]
    state_event = [event for event in rows if event["event_type"] in ("registered", "superseded", "withdrawn")][-1]
    hold = False
    for event in rows:
        if event["event_type"] == "legal_hold_placed": hold = True
        elif event["event_type"] == "legal_hold_released": hold = False
    return LifecycleState(snapshot_id, state_event["event_type"], latest["event_id"],latest["effective_at"],
      state_event["related_snapshot_id"] if state_event["event_type"] == "superseded" else None,
      state_event["event_type"] == "withdrawn", hold)


def add_retention_policy(conn: sqlite3.Connection, *, policy_id: str, version: int, dataset_type: str,
                         retention_days: int, effective_at: str, approval_reference: str, created_by: str) -> str:
    if not policy_id or version < 1 or not dataset_type or retention_days < 1 or not approval_reference or not created_by:
        raise LifecycleError("complete approved retention policy details are required")
    _dt(effective_at)
    body={"policy_id":policy_id,"version":version,"dataset_type":dataset_type,"retention_days":retention_days,
          "effective_at":effective_at,"approval_reference":approval_reference,"created_by":created_by}
    previous_cursor=conn.execute("SELECT version,effective_at FROM a13_retention_policies WHERE policy_id=? ORDER BY version DESC LIMIT 1",(policy_id,))
    previous=_mapping(previous_cursor,previous_cursor.fetchone())
    if previous and (version != previous["version"] + 1 or _dt(effective_at) <= _dt(previous["effective_at"])):
        raise LifecycleError("retention policy versions must increase sequentially with later effective dates")
    if not previous and version != 1:
        raise LifecycleError("first retention policy version must be 1")
    digest=canonical_hash(body)
    conn.execute("INSERT INTO a13_retention_policies VALUES (?,?,?,?,?,?,?,?)",
      (policy_id,version,dataset_type,retention_days,effective_at,approval_reference,created_by,digest))
    conn.commit(); return digest


def assign_retention_policy(conn: sqlite3.Connection, *, snapshot_id: str, policy_id: str, version: int,
                            assigned_at: str, assigned_by: str) -> None:
    decision=conn.execute("SELECT dataset_type FROM a13_snapshot_decisions WHERE snapshot_id=?",(snapshot_id,)).fetchone()
    policy=conn.execute("SELECT dataset_type FROM a13_retention_policies WHERE policy_id=? AND version=?",(policy_id,version)).fetchone()
    if not decision or not policy or decision[0] != policy[0]: raise LifecycleError("retention policy dataset mismatch or missing snapshot/policy")
    assigned_dt=_dt(assigned_at)
    policy_effective=conn.execute("SELECT effective_at FROM a13_retention_policies WHERE policy_id=? AND version=?",(policy_id,version)).fetchone()[0]
    if assigned_dt < _dt(policy_effective): raise LifecycleError("retention policy cannot be assigned before its effective date")
    conn.execute("INSERT INTO a13_snapshot_retention_assignments VALUES (?,?,?,?,?)",(snapshot_id,policy_id,version,assigned_at,assigned_by)); conn.commit()


def assess_retention(conn: sqlite3.Connection, *, snapshot_id: str, now: str) -> RetentionAssessment:
    current=_dt(now)
    assignment_cursor=conn.execute("SELECT a.policy_id,a.policy_version,p.retention_days,d.decided_at FROM a13_snapshot_retention_assignments a JOIN a13_retention_policies p ON p.policy_id=a.policy_id AND p.version=a.policy_version JOIN a13_snapshot_decisions d ON d.snapshot_id=a.snapshot_id WHERE a.snapshot_id=?",(snapshot_id,))
    assignment=_mapping(assignment_cursor,assignment_cursor.fetchone())
    state=lifecycle_state(conn,snapshot_id)
    raw=tuple(r[0] for r in conn.execute("SELECT raw_response_id FROM a13_snapshot_raw_links WHERE snapshot_id=? ORDER BY raw_response_id",(snapshot_id,)))
    if assignment:
        expiry=_dt(assignment["decided_at"])+timedelta(days=assignment["retention_days"])
        expired=current >= expiry
        return RetentionAssessment(snapshot_id,assignment["policy_id"],expiry.isoformat().replace("+00:00","Z"),expired,state.legal_hold,raw,False)
    return RetentionAssessment(snapshot_id,None,None,False,state.legal_hold,raw,False)

def supersede_snapshot_for_records(conn: sqlite3.Connection, *, old_snapshot_id: str,
                                   new_snapshot_id: str, old_event_id: str,
                                   new_event_id: str, effective_at: str,
                                   actor_id: str, reason: str, records,
                                   commit: bool = True) -> None:
    """Atomically supersede a snapshot and register its replacement with exact
    record-to-raw-response/source-index provenance.

    Unlike the legacy raw-response-only helper, this is the publication-safe
    replacement path. The replacement is not lifecycle-registered unless its
    approved membership, content hash, and per-record source coordinates pass.
    """
    from .read_gate import snapshot_content_hash

    rows = list(records)
    if not rows:
        raise LifecycleError("replacement snapshot requires approved evidence records")
    old = conn.execute(
        "SELECT dataset_type FROM a13_snapshot_decisions WHERE snapshot_id=?",
        (old_snapshot_id,),
    ).fetchone()
    new = conn.execute(
        "SELECT dataset_type, dataset_status, record_ids_json, snapshot_content_hash "
        "FROM a13_snapshot_decisions WHERE snapshot_id=?",
        (new_snapshot_id,),
    ).fetchone()
    if not old or not new:
        raise LifecycleError("both old and replacement decisions must exist")
    if old[0] != new[0] or new[1] not in ("approved_for_research", "approved_with_warnings"):
        raise LifecycleError("replacement must be approved and have the same dataset type")
    if set(json.loads(new[2])) != {record.record_id for record in rows}:
        raise LifecycleError("replacement records differ from approved snapshot membership")
    if len({record.record_id for record in rows}) != len(rows):
        raise LifecycleError("replacement records must have unique IDs")
    if snapshot_content_hash(rows) != new[3]:
        raise LifecycleError("replacement content or source provenance differs from approved snapshot")

    owns_transaction = not conn.in_transaction
    conn.execute("SAVEPOINT a13_supersede_snapshot_for_records")
    try:
        _append_event(
            conn, event_id=old_event_id, snapshot_id=old_snapshot_id,
            event_type="superseded", effective_at=effective_at,
            actor_id=actor_id, reason=reason, related_snapshot_id=new_snapshot_id,
        )
        register_snapshot_for_records(
            conn, snapshot_id=new_snapshot_id, event_id=new_event_id,
            effective_at=effective_at, actor_id=actor_id,
            reason=f"Replacement for {old_snapshot_id}: {reason}",
            records=rows, commit=False,
        )
        conn.execute("RELEASE SAVEPOINT a13_supersede_snapshot_for_records")
        if commit and owns_transaction:
            conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT a13_supersede_snapshot_for_records")
        conn.execute("RELEASE SAVEPOINT a13_supersede_snapshot_for_records")
        if commit and owns_transaction:
            conn.rollback()
        raise

