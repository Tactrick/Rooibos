-- Preserve the exact raw-response/source-record relationship for every A13 evidence record.
CREATE TABLE IF NOT EXISTS a13_snapshot_evidence_links (
    snapshot_id TEXT NOT NULL REFERENCES a13_snapshot_decisions(snapshot_id),
    record_id TEXT NOT NULL,
    raw_response_id TEXT NOT NULL REFERENCES raw_responses(raw_response_id),
    source_record_index INTEGER NOT NULL CHECK (source_record_index >= 0),
    linked_at TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_a13_evidence_links_provenance
    ON a13_snapshot_evidence_links(snapshot_id, raw_response_id, source_record_index);
CREATE TRIGGER IF NOT EXISTS trg_a13_evidence_links_no_update
BEFORE UPDATE ON a13_snapshot_evidence_links
BEGIN SELECT RAISE(ABORT, 'a13_snapshot_evidence_links is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_a13_evidence_links_no_delete
BEFORE DELETE ON a13_snapshot_evidence_links
BEGIN SELECT RAISE(ABORT, 'a13_snapshot_evidence_links is append-only'); END;
