from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Iterable

DB_FILENAME = "moby_market.db"
MIGRATION_VERSION = 8

MIGRATION_001_SQL = r"""
CREATE TABLE schema_registry (
    schema_version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    base_url TEXT,
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE source_endpoints (
    endpoint_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    endpoint_name TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'GET' CHECK (method IN ('GET','POST')),
    path TEXT NOT NULL,
    permission_class TEXT NOT NULL DEFAULT 'research_read',
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
    UNIQUE(source_id, endpoint_name)
);

CREATE TABLE collectors (
    collector_id TEXT PRIMARY KEY,
    collector_name TEXT NOT NULL,
    module_code TEXT NOT NULL,
    version TEXT NOT NULL,
    is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0,1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE assets (
    asset_id TEXT PRIMARY KEY,
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    symbol TEXT,
    name TEXT,
    first_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(chain, token_address)
);

CREATE TABLE markets (
    market_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    market_address TEXT,
    quote_asset_address TEXT,
    market_type TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(source_id, market_address)
);

CREATE TABLE collection_runs (
    run_id TEXT PRIMARY KEY,
    collector_id TEXT NOT NULL REFERENCES collectors(collector_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    endpoint_id TEXT REFERENCES source_endpoints(endpoint_id),
    collector_version TEXT NOT NULL,
    requested_scope_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'created','configured','collecting','raw_captured','parsed',
        'normalised','validating','completed','partial','incomplete',
        'failed','cancelled','quarantined'
    )),
    request_count INTEGER NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    response_count INTEGER NOT NULL DEFAULT 0 CHECK (response_count >= 0),
    page_count INTEGER NOT NULL DEFAULT 0 CHECK (page_count >= 0),
    records_received INTEGER NOT NULL DEFAULT 0 CHECK (records_received >= 0),
    records_parsed INTEGER NOT NULL DEFAULT 0 CHECK (records_parsed >= 0),
    records_rejected INTEGER NOT NULL DEFAULT 0 CHECK (records_rejected >= 0),
    error_summary TEXT,
    provenance_hash TEXT
);

CREATE TABLE collection_requests (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    request_sequence INTEGER NOT NULL CHECK (request_sequence >= 0),
    request_params_json TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    response_status INTEGER,
    error_code TEXT,
    UNIQUE(run_id, request_sequence)
);

CREATE TABLE pagination_state (
    pagination_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    cursor_value TEXT,
    page_number INTEGER NOT NULL DEFAULT 0 CHECK (page_number >= 0),
    state_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(run_id)
);

CREATE TABLE raw_responses (
    raw_response_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    request_id TEXT REFERENCES collection_requests(request_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    endpoint_id TEXT REFERENCES source_endpoints(endpoint_id),
    provider_timestamp TEXT,
    retrieved_at TEXT NOT NULL,
    http_status INTEGER,
    request_params_json TEXT NOT NULL,
    response_headers_json TEXT,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    content_type TEXT,
    page_sequence INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE ohlcv_candles (
    candle_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id),
    market_id TEXT REFERENCES markets(market_id),
    interval TEXT NOT NULL CHECK (interval IN ('1m','5m','15m','1h','4h','1d')),
    open_time_ms INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    normalization_version TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    raw_response_id TEXT NOT NULL REFERENCES raw_responses(raw_response_id),
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    quality_status TEXT NOT NULL DEFAULT 'unvalidated',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(asset_id, market_id, interval, open_time_ms, source_id, normalization_version)
);

CREATE TABLE normalization_events (
    normalization_event_id TEXT PRIMARY KEY,
    raw_response_id TEXT NOT NULL REFERENCES raw_responses(raw_response_id),
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    input_record_count INTEGER NOT NULL DEFAULT 0,
    output_record_count INTEGER NOT NULL DEFAULT 0,
    rejected_record_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE validation_runs (
    validation_run_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    validator_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN (
        'approved_for_research','approved_with_warnings','quarantined','rejected'
    )),
    summary_json TEXT
);

CREATE TABLE validation_results (
    validation_result_id TEXT PRIMARY KEY,
    validation_run_id TEXT NOT NULL REFERENCES validation_runs(validation_run_id),
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN (
        'valid','valid_with_warnings','rejected','incomplete','duplicate','conflicting'
    )),
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE data_quality_events (
    quality_event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    record_type TEXT,
    record_id TEXT,
    quality_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info','warning','error','critical')),
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE research_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    snapshot_type TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    normalization_version TEXT NOT NULL,
    validation_version TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    canonicalization_version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    coverage_status TEXT NOT NULL DEFAULT 'coverage_unknown'
        CHECK (coverage_status IN ('coverage_complete','coverage_partial','coverage_unknown','coverage_degraded')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE snapshot_records (
    snapshot_record_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES research_snapshots(snapshot_id),
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    UNIQUE(snapshot_id, record_type, record_id)
);

CREATE INDEX idx_collection_runs_status_started
    ON collection_runs(status, started_at);
CREATE INDEX idx_raw_responses_run
    ON raw_responses(run_id, retrieved_at);
CREATE INDEX idx_ohlcv_asset_interval_time
    ON ohlcv_candles(asset_id, interval, open_time_ms);
CREATE INDEX idx_validation_results_run
    ON validation_results(validation_run_id, outcome);
CREATE INDEX idx_snapshot_records_snapshot
    ON snapshot_records(snapshot_id);

CREATE TRIGGER raw_responses_no_update
BEFORE UPDATE ON raw_responses
BEGIN
    SELECT RAISE(ABORT, 'raw_responses are immutable');
END;

CREATE TRIGGER raw_responses_no_delete
BEFORE DELETE ON raw_responses
BEGIN
    SELECT RAISE(ABORT, 'raw_responses are immutable');
END;
"""

MIGRATION_002_SQL = r"""
-- SQLite treats NULL values as distinct in ordinary UNIQUE constraints.
-- This partial index enforces candle identity when market_id is unknown.
CREATE UNIQUE INDEX uq_ohlcv_null_market_candle
    ON ohlcv_candles(asset_id, interval, open_time_ms, source_id, normalization_version)
    WHERE market_id IS NULL;
"""

MIGRATION_003_SQL = r"""
-- Add canonical A6 metadata without changing the original candle identity.
ALTER TABLE ohlcv_candles ADD COLUMN candle_status TEXT NOT NULL DEFAULT 'unconfirmed'
    CHECK (candle_status IN ('closed','forming','unconfirmed'));
ALTER TABLE ohlcv_candles ADD COLUMN volume_unit TEXT;
ALTER TABLE ohlcv_candles ADD COLUMN source_record_index INTEGER;
ALTER TABLE ohlcv_candles ADD COLUMN endpoint_id TEXT REFERENCES source_endpoints(endpoint_id);
"""

MIGRATION_004_SQL = r"""
CREATE TABLE market_observations (
    observation_id TEXT PRIMARY KEY,
    chain TEXT NOT NULL,
    mint_address TEXT NOT NULL,
    market_id TEXT REFERENCES markets(market_id),
    pool_address TEXT,
    market_scope TEXT NOT NULL CHECK (market_scope IN ('token_aggregated','pool_specific','unknown')),
    metric_code TEXT NOT NULL,
    numeric_value REAL,
    text_value TEXT,
    value_status TEXT NOT NULL CHECK (value_status IN ('present','missing','unavailable','not_reported','not_applicable')),
    unit TEXT NOT NULL,
    quote_currency TEXT,
    window_seconds INTEGER CHECK (window_seconds IS NULL OR window_seconds > 0),
    measurement_kind TEXT NOT NULL CHECK (measurement_kind IN ('observed','estimated','unknown')),
    observed_at TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    endpoint_id TEXT NOT NULL REFERENCES source_endpoints(endpoint_id),
    run_id TEXT NOT NULL REFERENCES collection_runs(run_id),
    raw_response_id TEXT NOT NULL REFERENCES raw_responses(raw_response_id),
    source_record_index INTEGER NOT NULL CHECK (source_record_index >= 0),
    normalization_version TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK ((numeric_value IS NOT NULL AND text_value IS NULL AND value_status='present') OR
           (numeric_value IS NULL AND text_value IS NOT NULL AND value_status='present') OR
           (numeric_value IS NULL AND text_value IS NULL AND value_status<>'present'))
);

CREATE TABLE market_observation_comparisons (
    comparison_id TEXT PRIMARY KEY,
    chain TEXT NOT NULL,
    mint_address TEXT NOT NULL,
    metric_code TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('within_tolerance','disagreement','not_comparable','insufficient')),
    observation_ids_json TEXT NOT NULL,
    comparison_scope_json TEXT NOT NULL,
    tolerance_pct REAL NOT NULL CHECK (tolerance_pct >= 0),
    minimum_value REAL,
    maximum_value REAL,
    spread_pct REAL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_market_observations_asset_metric_time
    ON market_observations(chain,mint_address,metric_code,observed_at);
CREATE INDEX idx_market_observations_source_run
    ON market_observations(source_id,run_id);
CREATE INDEX idx_market_observations_market_metric
    ON market_observations(market_id,pool_address,metric_code);
CREATE INDEX idx_market_comparisons_asset_metric
    ON market_observation_comparisons(chain,mint_address,metric_code,created_at);

CREATE TRIGGER market_observation_comparisons_no_update
BEFORE UPDATE ON market_observation_comparisons
BEGIN SELECT RAISE(ABORT, 'market_observation_comparisons are append-only'); END;
CREATE TRIGGER market_observation_comparisons_no_delete
BEFORE DELETE ON market_observation_comparisons
BEGIN SELECT RAISE(ABORT, 'market_observation_comparisons are append-only'); END;

CREATE TRIGGER market_observations_no_update
BEFORE UPDATE ON market_observations
BEGIN SELECT RAISE(ABORT, 'market_observations are append-only'); END;
CREATE TRIGGER market_observations_no_delete
BEFORE DELETE ON market_observations
BEGIN SELECT RAISE(ABORT, 'market_observations are append-only'); END;
"""

MIGRATION_005_SQL = r"""
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

"""

MIGRATION_006_SQL = r"""
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

"""


MIGRATION_007_SQL = r"""
-- Bind every persisted A13 decision to the exact immutable evidence membership and content.
ALTER TABLE a13_snapshot_decisions ADD COLUMN snapshot_content_hash TEXT NOT NULL DEFAULT '';
"""

MIGRATION_008_SQL = Path(__file__).resolve().parents[1].joinpath("migrations", "008_a13_snapshot_lifecycle.sql").read_text(encoding="utf-8")


def _connect(path: str | Path) -> sqlite3.Connection:
    path = str(path)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

def migration_checksum(sql: str = MIGRATION_001_SQL) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()

def initialize_database(path: str | Path = DB_FILENAME) -> Path:
    """Create the database file and apply all bundled migrations."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        apply_migrations(conn)
    finally:
        conn.close()
    return db_path

def apply_migrations(conn: sqlite3.Connection, migrations: Iterable[tuple[int, str, str]] | None = None) -> None:
    """Apply migrations atomically; an interrupted/failed migration rolls back."""
    if migrations is None:
        migrations = [
            (1, "Initial shared collector schema", MIGRATION_001_SQL),
            (2, "Enforce OHLCV uniqueness when market is unknown", MIGRATION_002_SQL),
            (3, "Add canonical A6 OHLCV metadata", MIGRATION_003_SQL),
            (4, "Add append-only A2 market observations", MIGRATION_004_SQL),
            (5, "Add append-only A1 token discovery observations", MIGRATION_005_SQL),
            (6, "Add append-only A13 quality assessments and snapshot decisions", MIGRATION_006_SQL),
            (7, "Bind A13 decisions to exact snapshot content hashes", MIGRATION_007_SQL),
            (8, "Add append-only A13 snapshot lifecycle and retention governance", MIGRATION_008_SQL),
        ]

    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            checksum_sha256 TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
    """)

    for version, description, sql in sorted(migrations, key=lambda item: item[0]):
        checksum = migration_checksum(sql)
        existing = conn.execute(
            "SELECT checksum_sha256 FROM schema_migrations WHERE version = ?",
            (version,)
        ).fetchone()
        if existing:
            if existing["checksum_sha256"] != checksum:
                raise RuntimeError(f"Migration {version} checksum mismatch; applied migrations are immutable")
            continue

        try:
            conn.execute("BEGIN IMMEDIATE")
            # executescript commits implicitly, so execute individual statements instead.
            for statement in _split_sql_statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, description, checksum_sha256) VALUES (?,?,?)",
                (version, description, checksum)
            )
            conn.execute(
                "INSERT INTO schema_registry(schema_version, description, checksum_sha256) VALUES (?,?,?)",
                (version, description, checksum)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

def _split_sql_statements(script: str) -> list[str]:
    # sqlite3.complete_statement handles trigger bodies and embedded semicolons.
    statements, buffer = [], ""
    for line in script.splitlines():
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise ValueError("Incomplete SQL migration statement")
    return statements

def check_integrity(path: str | Path) -> dict:
    conn = _connect(path)
    try:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
        foreign_key_violations = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        return {
            "integrity_check": integrity,
            "foreign_key_violations": foreign_key_violations,
            "ok": integrity == ["ok"] and not foreign_key_violations,
        }
    finally:
        conn.close()

def backup_database(source_path: str | Path, backup_path: str | Path) -> Path:
    """Consistent SQLite backup using the online backup API."""
    source_path, backup_path = Path(source_path), Path(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    src = _connect(source_path)
    dst = _connect(backup_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return backup_path

def restore_database(backup_path: str | Path, destination_path: str | Path) -> Path:
    """Restore by SQLite backup API, then verify before returning."""
    backup_database(backup_path, destination_path)
    result = check_integrity(destination_path)
    if not result["ok"]:
        raise RuntimeError(f"Restored database failed integrity checks: {result}")
    return Path(destination_path)
