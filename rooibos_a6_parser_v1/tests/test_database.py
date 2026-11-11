import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path

from rooibos_db.database import (
    MIGRATION_001_SQL, MIGRATION_002_SQL, MIGRATION_003_SQL, MIGRATION_004_SQL,
    MIGRATION_005_SQL, MIGRATION_006_SQL, MIGRATION_007_SQL, MIGRATION_008_SQL,
    MIGRATION_009_SQL, MIGRATION_010_SQL, migration_checksum,
    apply_migrations, backup_database, check_integrity,
    initialize_database, restore_database, _connect
)

class Migration001Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = self.base / "moby_market.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_fresh_database_initializes_and_integrity_passes(self):
        initialize_database(self.db)
        result = check_integrity(self.db)
        self.assertTrue(result["ok"], result)
        conn = _connect(self.db)
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            self.assertEqual(version, 10)
            indexes = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )}
            self.assertIn("uq_ohlcv_null_market_candle", indexes)
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            for required in ("sources", "collectors", "collection_runs", "raw_responses",
                             "ohlcv_candles", "a6_candle_evidence_links", "a13_snapshot_evidence_links", "validation_runs", "research_snapshots", "market_observations", "market_observation_comparisons"):
                self.assertIn(required, tables)
        finally:
            conn.close()

    def test_migration_009_backfills_existing_candle_raw_evidence(self):
        conn = _connect(self.db)
        try:
            legacy_migrations = [
                (1, "Initial shared collector schema", MIGRATION_001_SQL),
                (2, "Enforce OHLCV uniqueness when market is unknown", MIGRATION_002_SQL),
                (3, "Add canonical A6 OHLCV metadata", MIGRATION_003_SQL),
                (4, "Add append-only A2 market observations", MIGRATION_004_SQL),
                (5, "Add append-only A1 token discovery observations", MIGRATION_005_SQL),
                (6, "Add append-only A13 quality assessments and snapshot decisions", MIGRATION_006_SQL),
                (7, "Bind A13 decisions to exact snapshot content hashes", MIGRATION_007_SQL),
                (8, "Add append-only A13 snapshot lifecycle and retention governance", MIGRATION_008_SQL),
            ]
            apply_migrations(conn, legacy_migrations)
            conn.execute(
                "INSERT INTO sources(source_id,source_name,source_type) VALUES ('legacy-source','Legacy','api')"
            )
            conn.execute(
                "INSERT INTO source_endpoints(endpoint_id,source_id,endpoint_name,method,path) "
                "VALUES ('legacy-endpoint','legacy-source','ohlcv','GET','/ohlcv')"
            )
            conn.execute(
                "INSERT INTO collectors(collector_id,collector_name,module_code,version) "
                "VALUES ('legacy-collector','Legacy A6','A6','1')"
            )
            conn.execute(
                "INSERT INTO collection_runs(run_id,collector_id,source_id,endpoint_id,collector_version,"
                "requested_scope_json,started_at,status) VALUES "
                "('legacy-run','legacy-collector','legacy-source','legacy-endpoint','1','{}',"
                "'2026-11-09T00:00:00Z','completed')"
            )
            conn.execute(
                "INSERT INTO assets(asset_id,chain,token_address,symbol) "
                "VALUES ('legacy-asset','solana','LegacyMint','LEG')"
            )
            conn.execute(
                "INSERT INTO raw_responses(raw_response_id,run_id,source_id,endpoint_id,retrieved_at,"
                "request_params_json,payload_json,payload_sha256) VALUES "
                "('legacy-raw','legacy-run','legacy-source','legacy-endpoint',"
                "'2026-11-09T00:00:00Z','{}','{}','legacy-hash')"
            )
            conn.execute(
                "INSERT INTO ohlcv_candles(candle_id,asset_id,market_id,interval,open_time_ms,source_id,"
                "normalization_version,open,high,low,close,volume,raw_response_id,run_id,"
                "source_record_index,endpoint_id) VALUES "
                "('legacy-candle','legacy-asset',NULL,'15m',1794000000000,'legacy-source',"
                "'norm-v1',1.0,2.0,0.5,1.5,100.0,'legacy-raw','legacy-run',3,'legacy-endpoint')"
            )
            conn.close()
            initialize_database(self.db)
            conn = _connect(self.db)
            links = conn.execute(
                "SELECT candle_id,raw_response_id,run_id,source_id,endpoint_id,source_record_index "
                "FROM a6_candle_evidence_links"
            ).fetchall()
            self.assertEqual(
                [tuple(row) for row in links],
                [("legacy-candle","legacy-raw","legacy-run","legacy-source","legacy-endpoint",3)],
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ohlcv_candles WHERE candle_id='legacy-candle'").fetchone()[0],
                1,
            )
            self.assertEqual(check_integrity(self.db)["ok"], True)
        finally:
            conn.close()

    def test_migration_is_idempotent(self):
        initialize_database(self.db)
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0], 10)
        finally:
            conn.close()

    def test_foreign_keys_are_enforced(self):
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""
                    INSERT INTO collection_runs
                    (run_id, collector_id, source_id, collector_version,
                     requested_scope_json, started_at, status)
                    VALUES ('r1','missing_collector','missing_source','1',
                            '{}','2026-11-09T00:00:00Z','created')
                """)
        finally:
            conn.close()

    def test_raw_response_is_immutable(self):
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            conn.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('s','source','api')")
            conn.execute("INSERT INTO collectors(collector_id,collector_name,module_code,version) VALUES ('c','collector','A6','1')")
            conn.execute("""
                INSERT INTO collection_runs
                (run_id,collector_id,source_id,collector_version,requested_scope_json,started_at,status)
                VALUES ('r','c','s','1','{}','2026-11-09T00:00:00Z','created')
            """)
            conn.execute("""
                INSERT INTO raw_responses
                (raw_response_id,run_id,source_id,retrieved_at,request_params_json,payload_json,payload_sha256)
                VALUES ('raw','r','s','2026-11-09T00:00:00Z','{}','{}','hash')
            """)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE raw_responses SET payload_json='changed' WHERE raw_response_id='raw'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM raw_responses WHERE raw_response_id='raw'")
        finally:
            conn.close()


    def test_null_market_candles_reject_duplicate_identity(self):
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            conn.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('s','source','api')")
            conn.execute("INSERT INTO collectors(collector_id,collector_name,module_code,version) VALUES ('c','collector','A6','1')")
            conn.execute("""INSERT INTO collection_runs
                (run_id,collector_id,source_id,collector_version,requested_scope_json,started_at,status)
                VALUES ('r','c','s','1','{}','2026-11-09T00:00:00Z','created')""")
            conn.execute("INSERT INTO assets(asset_id,chain,token_address,symbol) VALUES ('a','solana','mint','MET')")
            conn.execute("""INSERT INTO raw_responses
                (raw_response_id,run_id,source_id,retrieved_at,request_params_json,payload_json,payload_sha256)
                VALUES ('raw','r','s','2026-11-09T00:00:00Z','{}','{}','hash')""")
            base = ("a", None, "15m", 1794000000000, "s", "norm-v1",
                    1.0, 2.0, 0.5, 1.5, 100.0, "raw", "r")
            sql = """INSERT INTO ohlcv_candles
                (candle_id,asset_id,market_id,interval,open_time_ms,source_id,
                 normalization_version,open,high,low,close,volume,raw_response_id,run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
            conn.execute(sql, ("c1",) + base)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql, ("c2",) + base)
            changed_time = list(base)
            changed_time[3] += 900000
            conn.execute(sql, ("c3",) + tuple(changed_time))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ohlcv_candles").fetchone()[0], 2)
        finally:
            conn.close()

    def test_failed_migration_rolls_back_atomically(self):
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            broken_sql = """
                CREATE TABLE should_rollback (id INTEGER PRIMARY KEY);
                THIS IS NOT VALID SQL;
            """
            with self.assertRaises(sqlite3.OperationalError):
                apply_migrations(conn, [(11, "Injected failure test", broken_sql)])
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='should_rollback'"
            ).fetchone())
            self.assertIsNone(conn.execute(
                "SELECT version FROM schema_migrations WHERE version=11"
            ).fetchone())
        finally:
            conn.close()

    def test_backup_restore_round_trip(self):
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            conn.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('s','source','api')")
        finally:
            conn.close()
        backup = backup_database(self.db, self.base / "backup.db")
        restored = restore_database(backup, self.base / "restored.db")
        self.assertTrue(check_integrity(restored)["ok"])
        conn = _connect(restored)
        try:
            self.assertEqual(conn.execute("SELECT source_name FROM sources WHERE source_id='s'").fetchone()[0], "source")
        finally:
            conn.close()

    def test_migrations_001_to_007_checksums_preserved(self):
        from rooibos_db.database import migration_checksum, MIGRATION_002_SQL, MIGRATION_003_SQL, MIGRATION_004_SQL, MIGRATION_005_SQL, MIGRATION_006_SQL, MIGRATION_007_SQL
        expected = {
            1: "4bee443882750393551dbc71e0c508b8586187470ebbd49c61cadf174bd55db1",
            2: "67ad60b625839cdf79828050f5ac9766eb976af0c0de6a1fa4df06cb91f9c906",
            3: "9ddc8319173f767a1a600321161c85ced6d4913af61b09699262c34b288e5734",
            4: "a0b20229e3ef13c6428fcafe0f9081b0a5c0f213cb548191a57a2b945d494691",
            5: "5d0a2bb6dff5ce076a38905f9e0681314f75761836a332ad1c4d2301afb1759b",
            6: "00c25a8e4d41e0c7c1dcb8583492b4b4fe38c945a9bbfcd48cfdcb7ecb1ce29a",
            7: "2c9d23633c867be4b2828cece5d6dd55e48beda28cd0172efc6361dac557009a",
        }
        sqls = {1: MIGRATION_001_SQL, 2: MIGRATION_002_SQL, 3: MIGRATION_003_SQL,
                4: MIGRATION_004_SQL, 5: MIGRATION_005_SQL, 6: MIGRATION_006_SQL, 7: MIGRATION_007_SQL}
        self.assertEqual({v: migration_checksum(sqls[v]) for v in expected}, expected)

    def test_v1_8_example_database_upgrades_to_migrations_009_and_010(self):
        example_db = Path(__file__).resolve().parents[1] / "examples" / "moby_market_example.db"
        self.assertTrue(example_db.is_file(), "v1.8.0 example database fixture is missing")
        upgrade_db = self.base / "v1_8_upgrade.db"
        shutil.copy2(example_db, upgrade_db)

        conn = _connect(upgrade_db)
        try:
            before_version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            self.assertEqual(before_version, 8)
            before_candles = conn.execute("SELECT COUNT(*) FROM ohlcv_candles").fetchone()[0]
            expected_backfill = conn.execute(
                "SELECT COUNT(*) FROM ohlcv_candles WHERE source_record_index IS NOT NULL"
            ).fetchone()[0]
        finally:
            conn.close()

        initialize_database(upgrade_db)
        conn = _connect(upgrade_db)
        try:
            self.assertEqual(
                conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0],
                10,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ohlcv_candles").fetchone()[0],
                before_candles,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM a6_candle_evidence_links").fetchone()[0],
                expected_backfill,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM a13_snapshot_evidence_links").fetchone()[0],
                0,
            )
            self.assertEqual(check_integrity(upgrade_db)["ok"], True)
        finally:
            conn.close()

    def test_migrations_008_to_010_checksums_are_persisted_and_immutable(self):
        initialize_database(self.db)
        expected_sql = {
            8: MIGRATION_008_SQL,
            9: MIGRATION_009_SQL,
            10: MIGRATION_010_SQL,
        }
        conn = _connect(self.db)
        try:
            stored = {
                row["version"]: row["checksum_sha256"]
                for row in conn.execute(
                    "SELECT version, checksum_sha256 FROM schema_migrations "
                    "WHERE version BETWEEN 8 AND 10"
                )
            }
            self.assertEqual(
                stored,
                {version: migration_checksum(sql) for version, sql in expected_sql.items()},
            )
            for version, sql in expected_sql.items():
                with self.subTest(version=version):
                    with self.assertRaisesRegex(RuntimeError, "checksum"):
                        apply_migrations(
                            conn,
                            [(version, f"Changed migration {version}", sql + "\\n-- injected drift")],
                        )
            self.assertEqual(
                {
                    row["version"]: row["checksum_sha256"]
                    for row in conn.execute(
                        "SELECT version, checksum_sha256 FROM schema_migrations "
                        "WHERE version BETWEEN 8 AND 10"
                    )
                },
                stored,
            )
            self.assertEqual(check_integrity(self.db)["ok"], True)
        finally:
            conn.close()

    def test_checksum_drift_is_rejected(self):
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            with self.assertRaises(RuntimeError):
                apply_migrations(conn, [(1, "Changed migration", MIGRATION_001_SQL + "\n-- changed")])
        finally:
            conn.close()

if __name__ == "__main__":
    unittest.main(verbosity=2)
