"""Append-only SQLite persistence for A1, bound to immutable collection evidence."""
import json
import sqlite3
from dataclasses import asdict

from .models import DiscoveryObservation

MIGRATION_VERSION = 5

DDL = r'''CREATE TABLE IF NOT EXISTS token_discovery_observations (
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
CREATE INDEX IF NOT EXISTS idx_a1_chain_mint_observed
ON token_discovery_observations(chain, mint, observed_at);
CREATE INDEX IF NOT EXISTS idx_a1_source_run
ON token_discovery_observations(source_id, run_id);
CREATE TRIGGER IF NOT EXISTS trg_a1_no_update
BEFORE UPDATE ON token_discovery_observations BEGIN
    SELECT RAISE(ABORT, 'token_discovery_observations is append-only');
END;
CREATE TRIGGER IF NOT EXISTS trg_a1_no_delete
BEFORE DELETE ON token_discovery_observations BEGIN
    SELECT RAISE(ABORT, 'token_discovery_observations is append-only');
END;'''


def apply_migration_005(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def store_observation(conn: sqlite3.Connection, obs: DiscoveryObservation) -> bool:
    """Persist only against matching captured evidence; exact replay is idempotent.

    Returns True for a new observation and False for an exact replay. A reused
    observation key with any different persisted value is rejected.
    """
    c, m = obs.context, obs.metadata
    raw = conn.execute(
        """SELECT rr.run_id, rr.source_id, rr.endpoint_id, cr.status
           FROM raw_responses rr JOIN collection_runs cr ON cr.run_id=rr.run_id
           WHERE rr.raw_response_id=?""",
        (c.raw_response_id,),
    ).fetchone()
    if not raw:
        raise ValueError("A1 observation requires an existing immutable raw response")
    if (raw["run_id"], raw["source_id"], raw["endpoint_id"]) != (
        c.run_id, c.source_id, c.endpoint_id
    ):
        raise ValueError("A1 observation provenance does not match its raw response")
    if raw["status"] not in {"raw_captured", "parsed", "normalised", "validating", "partial", "completed"}:
        raise ValueError("A1 persistence requires a captured collection run")

    values = (
        obs.observation_key, c.chain, c.mint, c.source_id, c.endpoint_id, c.run_id,
        c.raw_response_id, c.source_record_index, c.observed_at, c.normalization_version,
        obs.provider_record_id, m.symbol, m.name, m.decimals, m.creator, m.created_at,
        m.first_seen_at, m.launchpad, m.website,
        json.dumps(m.social_links, sort_keys=True) if m.social_links is not None else None,
        json.dumps({k: ("not_reported" if v is None else "reported") for k, v in asdict(m).items()}, sort_keys=True),
        json.dumps(obs.raw_record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str),
        obs.content_hash,
    )
    columns = (
        "observation_key,chain,mint,source_id,endpoint_id,run_id,raw_response_id,"
        "source_record_index,observed_at,normalization_version,provider_record_id,"
        "symbol,name,decimals,creator,source_created_at,source_first_seen_at,launchpad,"
        "website,social_links_json,metadata_field_states_json,raw_record_json,content_hash"
    )
    conn.execute("SAVEPOINT a1_store_observation")
    try:
        existing = conn.execute(
            f"SELECT {columns} FROM token_discovery_observations WHERE observation_key=?",
            (obs.observation_key,),
        ).fetchone()
        if existing:
            if tuple(existing) != values:
                raise ValueError("A1 observation key already exists with different content or provenance")
            conn.execute("RELEASE SAVEPOINT a1_store_observation")
            conn.commit()
            return False
        conn.execute(
            f"""INSERT INTO token_discovery_observations ({columns})
                VALUES ({",".join("?" for _ in values)})""",
            values,
        )
        conn.execute("RELEASE SAVEPOINT a1_store_observation")
        conn.commit()
        return True
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT a1_store_observation")
        conn.execute("RELEASE SAVEPOINT a1_store_observation")
        raise
