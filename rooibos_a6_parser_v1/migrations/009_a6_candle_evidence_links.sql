-- Preserve every immutable raw observation that contributed an identical canonical candle.
CREATE TABLE a6_candle_evidence_links (
    candle_id TEXT NOT NULL REFERENCES ohlcv_candles(candle_id),
    raw_response_id TEXT NOT NULL REFERENCES raw_responses(raw_response_id),
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    endpoint_id TEXT REFERENCES source_endpoints(endpoint_id),
    source_record_index INTEGER NOT NULL CHECK(source_record_index >= 0),
    linked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(candle_id, raw_response_id, source_record_index)
);
CREATE INDEX idx_a6_candle_evidence_raw
    ON a6_candle_evidence_links(raw_response_id, source_record_index);
CREATE TRIGGER trg_a6_candle_evidence_no_update
BEFORE UPDATE ON a6_candle_evidence_links
BEGIN SELECT RAISE(ABORT, 'a6_candle_evidence_links is append-only'); END;
CREATE TRIGGER trg_a6_candle_evidence_no_delete
BEFORE DELETE ON a6_candle_evidence_links
BEGIN SELECT RAISE(ABORT, 'a6_candle_evidence_links is append-only'); END;
-- Backfill the original single-evidence relationship for existing canonical candles.
INSERT INTO a6_candle_evidence_links(
    candle_id, raw_response_id, run_id, source_id, endpoint_id, source_record_index
)
SELECT candle_id, raw_response_id, run_id, source_id, endpoint_id, source_record_index
FROM ohlcv_candles
WHERE source_record_index IS NOT NULL;
