import sqlite3
import unittest

from rooibos_collectors.record_manifest import (
    load_verified_manifest,
    store_record_manifest,
)


class CollectionRecordManifestTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE collectors (collector_id TEXT PRIMARY KEY, module_code TEXT);
            CREATE TABLE collection_runs (
                run_id TEXT PRIMARY KEY, collector_id TEXT, status TEXT
            );
            CREATE TABLE raw_responses (
                raw_response_id TEXT PRIMARY KEY, run_id TEXT
            );
            CREATE TABLE collection_record_manifests (
                run_id TEXT PRIMARY KEY, raw_response_id TEXT UNIQUE,
                collector_module TEXT, source_record_count INTEGER,
                membership_json TEXT, manifest_sha256 TEXT
            );
            INSERT INTO collectors VALUES ('collector-a1', 'A1');
            INSERT INTO collection_runs VALUES ('run-1', 'collector-a1', 'normalised');
            INSERT INTO raw_responses VALUES ('raw-1', 'run-1');
        """)

    def tearDown(self):
        self.conn.close()

    def test_round_trip_preserves_exact_source_index_membership(self):
        store_record_manifest(
            self.conn, run_id="run-1", raw_response_id="raw-1",
            collector_module="A1", source_record_count=3,
            membership={0: ["obs-0"], 1: ["obs-1a", "obs-1b"], 2: []},
        )
        self.assertEqual(
            load_verified_manifest(
                self.conn, run_id="run-1", raw_response_id="raw-1",
                collector_module="A1",
            ),
            {0: ("obs-0",), 1: ("obs-1a", "obs-1b"), 2: ()},
        )

    def test_rejects_missing_source_index(self):
        with self.assertRaisesRegex(ValueError, "every original"):
            store_record_manifest(
                self.conn, run_id="run-1", raw_response_id="raw-1",
                collector_module="A1", source_record_count=2,
                membership={0: ["obs-0"]},
            )

    def test_rejects_manifest_for_wrong_raw_response(self):
        with self.assertRaisesRegex(ValueError, "does not belong"):
            store_record_manifest(
                self.conn, run_id="run-1", raw_response_id="raw-other",
                collector_module="A1", source_record_count=1,
                membership={0: ["obs-0"]},
            )

    def test_rejects_manifest_hash_tampering(self):
        store_record_manifest(
            self.conn, run_id="run-1", raw_response_id="raw-1",
            collector_module="A1", source_record_count=1,
            membership={0: ["obs-0"]},
        )
        self.conn.execute(
            "UPDATE collection_record_manifests SET membership_json='{}' WHERE run_id='run-1'"
        )
        with self.assertRaisesRegex(ValueError, "hash verification"):
            load_verified_manifest(
                self.conn, run_id="run-1", raw_response_id="raw-1",
                collector_module="A1",
            )


if __name__ == "__main__":
    unittest.main()
