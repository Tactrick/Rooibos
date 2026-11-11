-- Immutable source-record coverage receipt for publication-only recovery.
-- Publication failures are tracked separately from the run status so a generic\n-- failed run can never be mistaken for a retryable publication failure.\nALTER TABLE collection_runs ADD COLUMN publication_failed_at TEXT;\nALTER TABLE collection_runs ADD COLUMN publication_failure_summary TEXT;\n\n-- One receipt per single-response A1/A2/A6 collection run. The canonical JSON
-- records the complete source-index -> normalized-record-ID mapping established
-- during initial normalization, before A13 publication is attempted.
CREATE TABLE collection_record_manifests (
    run_id TEXT PRIMARY KEY REFERENCES collection_runs(run_id),
    raw_response_id TEXT NOT NULL UNIQUE REFERENCES raw_responses(raw_response_id),
    collector_module TEXT NOT NULL CHECK (collector_module IN ('A1','A2','A6')),
    source_record_count INTEGER NOT NULL CHECK (source_record_count > 0),
    membership_json TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TRIGGER collection_record_manifests_no_update
BEFORE UPDATE ON collection_record_manifests
BEGIN SELECT RAISE(ABORT, 'collection_record_manifests are immutable'); END;
CREATE TRIGGER collection_record_manifests_no_delete
BEFORE DELETE ON collection_record_manifests
BEGIN SELECT RAISE(ABORT, 'collection_record_manifests are immutable'); END;
