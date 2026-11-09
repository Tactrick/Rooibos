"""Append-only storage for A13 assessments and snapshot decisions."""
import json, sqlite3
from dataclasses import asdict
from .models import RecordAssessment, SnapshotDecision

DDL = r'''CREATE TABLE IF NOT EXISTS a13_record_assessments (
 assessment_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, status TEXT NOT NULL,
 findings_json TEXT NOT NULL, assessed_at TEXT NOT NULL, validator_version TEXT NOT NULL,
 assessment_hash TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS idx_a13_record_assessments_record ON a13_record_assessments(record_id, assessed_at);
CREATE TABLE IF NOT EXISTS a13_snapshot_decisions (
 snapshot_id TEXT PRIMARY KEY, dataset_type TEXT NOT NULL, record_ids_json TEXT NOT NULL,
 dataset_status TEXT NOT NULL, assessments_hash TEXT NOT NULL, findings_json TEXT NOT NULL,
 decided_at TEXT NOT NULL, policy_version TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS trg_a13_assessments_no_update BEFORE UPDATE ON a13_record_assessments BEGIN SELECT RAISE(ABORT,'a13_record_assessments is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_a13_assessments_no_delete BEFORE DELETE ON a13_record_assessments BEGIN SELECT RAISE(ABORT,'a13_record_assessments is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_a13_snapshots_no_update BEFORE UPDATE ON a13_snapshot_decisions BEGIN SELECT RAISE(ABORT,'a13_snapshot_decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_a13_snapshots_no_delete BEFORE DELETE ON a13_snapshot_decisions BEGIN SELECT RAISE(ABORT,'a13_snapshot_decisions is append-only'); END;'''

def apply_migration_006(conn: sqlite3.Connection):
    conn.executescript(DDL); conn.commit()

def store_assessment(conn: sqlite3.Connection, assessment: RecordAssessment, assessment_id: str, assessment_hash: str) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO a13_record_assessments VALUES (?,?,?,?,?,?,?)", (assessment_id, assessment.record_id, assessment.status, json.dumps([asdict(x) for x in assessment.findings], sort_keys=True), assessment.assessed_at, assessment.validator_version, assessment_hash))
    conn.commit(); return cur.rowcount == 1

def store_decision(conn: sqlite3.Connection, decision: SnapshotDecision) -> bool:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(a13_snapshot_decisions)")}
    snapshot_hash = getattr(decision, "snapshot_hash", "")
    if "snapshot_content_hash" not in columns:
        raise RuntimeError("A13 migration 007 is required: snapshot content hash must be persisted")
    values = (decision.snapshot_id, decision.dataset_type, json.dumps(decision.record_ids),
              decision.dataset_status, decision.assessments_hash,
              json.dumps([asdict(x) for x in decision.findings], sort_keys=True),
              decision.decided_at, decision.policy_version, snapshot_hash)
    existing = conn.execute("SELECT dataset_type, record_ids_json, dataset_status, assessments_hash, findings_json, decided_at, policy_version, snapshot_content_hash FROM a13_snapshot_decisions WHERE snapshot_id=?", (decision.snapshot_id,)).fetchone()
    if existing is not None:
        if tuple(existing) != values[1:]:
            raise ValueError("snapshot_id already exists with a different immutable decision")
        return False
    conn.execute("INSERT INTO a13_snapshot_decisions (snapshot_id,dataset_type,record_ids_json,dataset_status,assessments_hash,findings_json,decided_at,policy_version,snapshot_content_hash) VALUES (?,?,?,?,?,?,?,?,?)", values)
    conn.commit(); return True
