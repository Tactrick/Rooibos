import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DB_ROOT))

from rooibos_db.database import initialize_database, check_integrity
from rooibos_a6.parser import ParseContext, normalise_records
from rooibos_a6.storage import store_raw_response, persist_parse_result
from rooibos_integrations.a13_projection import project_a6_from_storage, publish_a6_snapshot_from_storage
from rooibos_a13.models import SourcePolicy


class A6StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'moby_market.db'
        initialize_database(self.db)
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('moby','Moby','api')")
        c.execute("INSERT INTO source_endpoints(endpoint_id,source_id,endpoint_name,path) VALUES ('ohlcv','moby','get_token_ohlcv','/ohlcv')")
        c.execute("INSERT INTO collectors(collector_id,collector_name,module_code,version) VALUES ('A6','A6 OHLCV','A6','1.0.0')")
        c.execute("INSERT INTO assets(asset_id,chain,token_address,symbol) VALUES ('asset1','solana','MintABC','TST')")
        c.execute("""INSERT INTO collection_runs(
            run_id,collector_id,source_id,endpoint_id,collector_version,requested_scope_json,started_at,status
        ) VALUES ('run1','A6','moby','ohlcv','1.0.0','{}','2026-11-09T10:00:00Z','collecting')""")
        c.commit(); c.close()
        self.payload = {"synthetic_fixture": True, "candles": 2}
        self.raw_id = 'raw1'
        store_raw_response(
            self.db, raw_response_id=self.raw_id, run_id='run1', source_id='moby', endpoint_id='ohlcv',
            payload=self.payload, request_params={"mint":"MintABC","interval":"15m","limit":2},
            retrieved_at='2026-11-09T10:00:01Z', http_status=200,
        )
        c = sqlite3.connect(self.db)
        c.execute("UPDATE collection_runs SET status='parsed' WHERE run_id='run1'")
        c.commit(); c.close()
        self.context = ParseContext(
            chain='solana',asset_id='asset1',mint_address='MintABC',interval='15m',source_id='moby',
            endpoint_id='ohlcv',collection_run_id='run1',raw_response_ref=self.raw_id, volume_unit='base_token',
        )
        self.records = [
            {"timestamp_ms": 1794225600000, "open": 1, "high": 1.2, "low": .9, "close": 1.1, "volume": 10, "candle_status":"closed"},
            {"timestamp_ms": 1794226500000, "open": 1.1, "high": 1.3, "low": 1, "close": 1.2, "volume": 12, "candle_status":"closed"},
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, records=None):
        return normalise_records(self.records if records is None else records, self.context)

    def test_raw_then_parse_persists_unvalidated_candles_and_manifest(self):
        result = self.parse()
        outcome = persist_parse_result(self.db, result=result, raw_response_id=self.raw_id)
        self.assertEqual(outcome['inserted_count'], 2)
        self.assertEqual(outcome['status'], 'persisted_unvalidated')
        c = sqlite3.connect(self.db)
        rows = c.execute("SELECT quality_status,candle_status,source_record_index,endpoint_id FROM ohlcv_candles ORDER BY open_time_ms").fetchall()
        self.assertEqual(rows, [('unvalidated','closed',0,'ohlcv'),('unvalidated','closed',1,'ohlcv')])
        event = c.execute("SELECT status,details_json FROM normalization_events").fetchone()
        self.assertEqual(event[0], 'completed')
        self.assertIn('output_sha256', event[1])
        self.assertEqual(c.execute("SELECT status FROM collection_runs WHERE run_id='run1'").fetchone()[0], 'normalised')
        c.close()

    def test_replay_is_idempotent(self):
        result = self.parse()
        first = persist_parse_result(self.db, result=result, raw_response_id=self.raw_id)
        second = persist_parse_result(self.db, result=result, raw_response_id=self.raw_id)
        self.assertEqual(first['inserted_count'], 2)
        self.assertEqual(second['inserted_count'], 0)
        self.assertEqual(second['existing_identical_count'], 2)
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM ohlcv_candles').fetchone()[0], 2)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM normalization_events').fetchone()[0], 2)
        c.close()

    def test_raw_response_is_immutable_and_idempotent(self):
        same_hash = store_raw_response(
            self.db, raw_response_id=self.raw_id, run_id='run1', source_id='moby', endpoint_id='ohlcv',
            payload=self.payload, request_params={"mint":"MintABC","interval":"15m","limit":2},
            retrieved_at='2026-11-09T10:00:01Z', http_status=200,
        )
        self.assertTrue(same_hash)
        with self.assertRaises(ValueError):
            store_raw_response(
                self.db, raw_response_id=self.raw_id, run_id='run1', source_id='moby', endpoint_id='ohlcv',
                payload={"changed":True}, request_params={},
            )
        with self.assertRaises(ValueError):
            store_raw_response(
                self.db, raw_response_id=self.raw_id, run_id='run1', source_id='moby', endpoint_id='ohlcv',
                payload=self.payload, request_params={"different":True},
                retrieved_at='2026-11-09T10:00:01Z', http_status=200,
            )
        c = sqlite3.connect(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE raw_responses SET payload_json='{}' WHERE raw_response_id='raw1'")
        c.close()

    def test_parser_conflict_variants_are_kept_in_manifest_not_canonical_table(self):
        conflicting = [self.records[0], dict(self.records[0], close=1.15)]
        result = self.parse(conflicting)
        self.assertEqual(result.conflict_count, 1)
        outcome = persist_parse_result(self.db, result=result, raw_response_id=self.raw_id)
        self.assertEqual(outcome['parser_conflict_variant_count'], 1)
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM ohlcv_candles').fetchone()[0], 0)
        details = c.execute('SELECT details_json FROM normalization_events').fetchone()[0]
        self.assertIn('CONFLICTING_CANDLE', details)
        self.assertIn('1.15', details)
        c.close()

    def test_unregistered_asset_fails_without_partial_write(self):
        ctx = ParseContext(
            chain='solana',asset_id='missing',mint_address='MissingMint',interval='15m',source_id='moby',
            endpoint_id='ohlcv',collection_run_id='run1',raw_response_ref=self.raw_id,
        )
        result = normalise_records(self.records[:1], ctx)
        with self.assertRaises(ValueError):
            persist_parse_result(self.db, result=result, raw_response_id=self.raw_id)
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM ohlcv_candles').fetchone()[0], 0)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM normalization_events').fetchone()[0], 0)
        c.close()

    def test_database_integrity_after_storage(self):
        persist_parse_result(self.db, result=self.parse(), raw_response_id=self.raw_id)
        self.assertTrue(check_integrity(self.db)['ok'])



    def test_repeated_identical_candle_retains_all_raw_evidence_for_a13(self):
        first = persist_parse_result(self.db, result=self.parse(), raw_response_id=self.raw_id)
        self.assertEqual(first["inserted_count"], 2)

        c = sqlite3.connect(self.db)
        c.execute("""INSERT INTO collection_runs(
            run_id,collector_id,source_id,endpoint_id,collector_version,requested_scope_json,started_at,status
        ) VALUES ('run2','A6','moby','ohlcv','1.0.0','{}','2026-11-09T10:01:00Z','collecting')""")
        c.commit()
        c.close()

        second_raw_id = "raw2"
        store_raw_response(
            self.db, raw_response_id=second_raw_id, run_id="run2", source_id="moby",
            endpoint_id="ohlcv", payload={"synthetic_fixture": True, "candles": 2},
            request_params={"mint":"MintABC","interval":"15m","limit":2},
            retrieved_at="2026-11-09T10:01:01Z", http_status=200,
        )
        c = sqlite3.connect(self.db)
        c.execute("UPDATE collection_runs SET status='parsed' WHERE run_id='run2'")
        c.commit()
        c.close()

        context2 = ParseContext(
            chain="solana", asset_id="asset1", mint_address="MintABC", interval="15m",
            source_id="moby", endpoint_id="ohlcv", collection_run_id="run2",
            raw_response_ref=second_raw_id, volume_unit="base_token",
        )
        result2 = normalise_records(self.records, context2)
        second = persist_parse_result(self.db, result=result2, raw_response_id=second_raw_id)
        self.assertEqual(second["inserted_count"], 0)
        self.assertEqual(second["existing_identical_count"], 2)

        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        candle_id = c.execute("SELECT candle_id FROM ohlcv_candles ORDER BY open_time_ms LIMIT 1").fetchone()["candle_id"]
        evidence = c.execute(
            "SELECT raw_response_id,run_id,source_record_index FROM a6_candle_evidence_links WHERE candle_id=? ORDER BY raw_response_id",
            (candle_id,),
        ).fetchall()
        self.assertEqual({row["raw_response_id"] for row in evidence}, {"raw1", "raw2"})
        projected = project_a6_from_storage(c, candle_id)
        self.assertEqual({record.raw_response_id for record in projected}, {"raw1", "raw2"})
        self.assertEqual(len(projected), 2)

        all_candle_ids = [row["candle_id"] for row in c.execute(
            "SELECT candle_id FROM ohlcv_candles ORDER BY open_time_ms"
        )]
        records, assessments, decision, grant = publish_a6_snapshot_from_storage(
            c, all_candle_ids, snapshot_id="a6-repeat-published",
            source_policies={"moby": SourcePolicy("moby", ("ohlcv",), True)},
            assessed_at="2026-11-09T10:02:00Z",
            decided_at="2026-11-09T10:03:00Z",
            effective_at="2026-11-09T10:04:00Z",
            actor_id="test-governance", reason="publish repeated synthetic observations",
        )
        self.assertIsNotNone(grant)
        linked_raw = {row[0] for row in c.execute(
            "SELECT raw_response_id FROM a13_snapshot_raw_links WHERE snapshot_id=?",
            ("a6-repeat-published",),
        )}
        self.assertEqual(linked_raw, {"raw1", "raw2"})
        self.assertEqual({record.raw_response_id for record in records}, {"raw1", "raw2"})
        c.close()


    def test_a6_storage_to_a13_publication_and_read_gate_uses_all_evidence(self):
        persist_parse_result(self.db, result=self.parse(), raw_response_id=self.raw_id)
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        candle_ids = [row["candle_id"] for row in c.execute(
            "SELECT candle_id FROM ohlcv_candles ORDER BY open_time_ms"
        )]
        records, assessments, decision, grant = publish_a6_snapshot_from_storage(
            c, candle_ids, snapshot_id="a6-storage-published",
            source_policies={"moby": SourcePolicy("moby", ("ohlcv",), True)},
            assessed_at="2026-11-09T10:02:00Z",
            decided_at="2026-11-09T10:03:00Z",
            effective_at="2026-11-09T10:04:00Z",
            actor_id="test-governance", reason="publish synthetic A6 fixture",
        )
        self.assertIn(decision.dataset_status, ("approved_for_research", "approved_with_warnings"))
        self.assertIsNotNone(grant)
        self.assertEqual(set(grant.record_ids), {record.record_id for record in records})
        expected_raw = {record.raw_response_id for record in records}
        linked_raw = {row[0] for row in c.execute(
            "SELECT raw_response_id FROM a13_snapshot_raw_links WHERE snapshot_id=?",
            ("a6-storage-published",),
        )}
        self.assertEqual(linked_raw, expected_raw)
        self.assertEqual(expected_raw, {"raw1"})
        c.close()


    def test_a13_publication_rolls_back_everything_if_read_gate_fails_after_lifecycle_registration(self):
        persist_parse_result(self.db, result=self.parse(), raw_response_id=self.raw_id)
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        candle_ids = [row["candle_id"] for row in c.execute(
            "SELECT candle_id FROM ohlcv_candles ORDER BY open_time_ms"
        )]

        # Inject failure at the final gate, after assessment, decision,
        # lifecycle registration and raw-evidence linking have been staged.
        from unittest.mock import patch
        with patch("rooibos_a13.read_gate.authorize_snapshot",
                   side_effect=RuntimeError("injected final read-gate failure")):
            with self.assertRaisesRegex(RuntimeError, "injected final read-gate failure"):
                publish_a6_snapshot_from_storage(
                    c, candle_ids, snapshot_id="a6-atomic-failure",
                    source_policies={"moby": SourcePolicy("moby", ("ohlcv",), True)},
                    assessed_at="2026-11-09T10:02:00Z",
                    decided_at="2026-11-09T10:03:00Z",
                    effective_at="2026-11-09T10:04:00Z",
                    actor_id="test-governance", reason="exercise atomic rollback",
                )

        # Assessments are keyed by record, not snapshot; compare their
        # global count as this isolated fixture has no prior A13 assessments.
        self.assertEqual(c.execute(
            "SELECT COUNT(*) FROM a13_record_assessments"
        ).fetchone()[0], 0, "assessment rows survived failed publication")
        for table in (
            "a13_snapshot_decisions",
            "a13_snapshot_lifecycle_events",
            "a13_snapshot_raw_links",
        ):
            count = c.execute(
                f"SELECT COUNT(*) FROM {table} WHERE snapshot_id=?",
                ("a6-atomic-failure",),
            ).fetchone()[0]
            self.assertEqual(count, 0, f"{table} retained partial publication rows")
        c.close()

if __name__ == '__main__':
    unittest.main()
