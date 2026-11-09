-- Bind every persisted A13 decision to the exact immutable evidence membership and content.
ALTER TABLE a13_snapshot_decisions ADD COLUMN snapshot_content_hash TEXT NOT NULL DEFAULT '';
