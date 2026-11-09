-- Append-only lifecycle, retention policy and raw-evidence linkage governance.
CREATE TABLE a13_snapshot_lifecycle_events (
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
CREATE INDEX idx_a13_lifecycle_snapshot_sequence ON a13_snapshot_lifecycle_events(snapshot_id, sequence_no);
CREATE TABLE a13_retention_policies (
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
CREATE TABLE a13_snapshot_retention_assignments (
  snapshot_id TEXT PRIMARY KEY REFERENCES a13_snapshot_decisions(snapshot_id),
  policy_id TEXT NOT NULL,
  policy_version INTEGER NOT NULL,
  assigned_at TEXT NOT NULL,
  assigned_by TEXT NOT NULL,
  FOREIGN KEY(policy_id, policy_version) REFERENCES a13_retention_policies(policy_id, version)
);
CREATE TABLE a13_snapshot_raw_links (
  snapshot_id TEXT NOT NULL REFERENCES a13_snapshot_decisions(snapshot_id),
  raw_response_id TEXT NOT NULL REFERENCES raw_responses(raw_response_id),
  linked_at TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, raw_response_id)
);
CREATE TRIGGER trg_a13_lifecycle_no_update BEFORE UPDATE ON a13_snapshot_lifecycle_events BEGIN SELECT RAISE(ABORT,'a13_snapshot_lifecycle_events is append-only'); END;
CREATE TRIGGER trg_a13_lifecycle_no_delete BEFORE DELETE ON a13_snapshot_lifecycle_events BEGIN SELECT RAISE(ABORT,'a13_snapshot_lifecycle_events is append-only'); END;
CREATE TRIGGER trg_a13_retention_policy_no_update BEFORE UPDATE ON a13_retention_policies BEGIN SELECT RAISE(ABORT,'a13_retention_policies is append-only'); END;
CREATE TRIGGER trg_a13_retention_policy_no_delete BEFORE DELETE ON a13_retention_policies BEGIN SELECT RAISE(ABORT,'a13_retention_policies is append-only'); END;
CREATE TRIGGER trg_a13_retention_assignment_no_update BEFORE UPDATE ON a13_snapshot_retention_assignments BEGIN SELECT RAISE(ABORT,'a13_snapshot_retention_assignments is append-only'); END;
CREATE TRIGGER trg_a13_retention_assignment_no_delete BEFORE DELETE ON a13_snapshot_retention_assignments BEGIN SELECT RAISE(ABORT,'a13_snapshot_retention_assignments is append-only'); END;
CREATE TRIGGER trg_a13_raw_links_no_update BEFORE UPDATE ON a13_snapshot_raw_links BEGIN SELECT RAISE(ABORT,'a13_snapshot_raw_links is append-only'); END;
CREATE TRIGGER trg_a13_raw_links_no_delete BEFORE DELETE ON a13_snapshot_raw_links BEGIN SELECT RAISE(ABORT,'a13_snapshot_raw_links is append-only'); END;
