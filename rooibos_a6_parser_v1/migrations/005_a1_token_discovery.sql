-- Standalone migration body; safe to apply only after the existing 001-004 baseline.
-- No foreign keys to pre-existing tables are assumed because their exact schemas are not in this add-on.
CREATE TABLE IF NOT EXISTS token_discovery_observations (
    observation_key TEXT PRIMARY KEY,
    chain TEXT NOT NULL,
    mint TEXT NOT NULL,
    source_id TEXT NOT NULL,
    endpoint_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    raw_response_id TEXT NOT NULL,
    source_record_index INTEGER NOT NULL CHECK(source_record_index >= 0),
    observed_at TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    provider_record_id TEXT,
    symbol TEXT,
    name TEXT,
    decimals INTEGER CHECK(decimals IS NULL OR (decimals >= 0 AND decimals <= 255)),
    creator TEXT,
    source_created_at TEXT,
    source_first_seen_at TEXT,
    launchpad TEXT,
    website TEXT,
    social_links_json TEXT,
    metadata_field_states_json TEXT NOT NULL,
    raw_record_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_a1_chain_mint_observed ON token_discovery_observations(chain,mint,observed_at);
CREATE INDEX IF NOT EXISTS idx_a1_source_run ON token_discovery_observations(source_id,run_id);
CREATE TRIGGER IF NOT EXISTS trg_a1_no_update BEFORE UPDATE ON token_discovery_observations BEGIN SELECT RAISE(ABORT,'token_discovery_observations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_a1_no_delete BEFORE DELETE ON token_discovery_observations BEGIN SELECT RAISE(ABORT,'token_discovery_observations is append-only'); END;
