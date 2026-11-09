-- Apply after migrations 001-005. Additive only; no A1/A2/A6 records are modified.
CREATE TABLE IF NOT EXISTS a13_record_assessments (
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
CREATE TRIGGER IF NOT EXISTS trg_a13_snapshots_no_delete BEFORE DELETE ON a13_snapshot_decisions BEGIN SELECT RAISE(ABORT,'a13_snapshot_decisions is append-only'); END;
