import sqlite3
import tempfile
import unittest
from pathlib import Path

from rooibos_db.database import initialize_database, _connect, MIGRATION_VERSION
from rooibos_a1.models import DiscoveryContext, normalize_observation as normalize_a1
from rooibos_a1.storage import store_observation as store_a1_observation
from rooibos_a2.normalizer import MarketMetricInput, MarketObservationInput, normalize_observation as normalize_a2
from rooibos_a6.parser import ParseContext, normalise_records
from rooibos_a13.models import SourcePolicy
from rooibos_a13.read_gate import authorize_snapshot, snapshot_content_hash, SnapshotAccessDenied
from rooibos_integrations.a13_projection import project_a1, project_a2, project_a6, assess_and_store_snapshot
from rooibos_a13.lifecycle import register_snapshot_for_records

MINT = "So11111111111111111111111111111111111111112"
POLICIES = {"fixture_source": SourcePolicy("fixture_source", ("fixture_endpoint",), True)}

class A13PipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "moby_market.db"
        initialize_database(self.db)
        self.conn = _connect(self.db)

    def tearDown(self):
        self.conn.close(); self.tmp.cleanup()

    def test_migration_chain_001_to_010_and_legacy_schema(self):
        versions = [r[0] for r in self.conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(versions, [1,2,3,4,5,6,7,8,9,10])
        self.assertEqual(MIGRATION_VERSION, 10)
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"ohlcv_candles", "a6_candle_evidence_links", "market_observations", "token_discovery_observations",
                         "a13_record_assessments", "a13_snapshot_decisions", "a13_snapshot_lifecycle_events", "a13_snapshot_evidence_links", "a13_retention_policies"}.issubset(tables))
        self.assertIn("snapshot_content_hash", {r[1] for r in self.conn.execute("PRAGMA table_info(a13_snapshot_decisions)")})

    def test_a1_projection_and_snapshot_gate(self):
        ctx = DiscoveryContext("solana", MINT, "fixture_source", "fixture_endpoint", "run-a1",
                               "raw-a1", 0, "2026-11-09T10:00:00Z")
        obs = normalize_a1(ctx, {"symbol":"SOL", "name":"Solana"}, field_map={"symbol":"symbol", "name":"name"})
        record = project_a1(obs)
        assessments, decision = assess_and_store_snapshot(self.conn, [record], snapshot_id="snap-a1",
            source_policies=POLICIES, assessed_at="2026-11-09T10:01:00Z", decided_at="2026-11-09T10:02:00Z")
        self.assertEqual(decision.dataset_status, "approved_for_research")
        self.conn.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('fixture_source','Fixture','api')")
        self.conn.execute("INSERT INTO collectors(collector_id,collector_name,module_code,version) VALUES ('fixture-collector','Fixture','A1','1')")
        self.conn.execute("INSERT INTO collection_runs(run_id,collector_id,source_id,collector_version,requested_scope_json,started_at,status) VALUES ('run-a1','fixture-collector','fixture_source','1','{}','2026-11-09T10:00:00Z','completed')")
        self.conn.execute("INSERT INTO raw_responses(raw_response_id,run_id,source_id,endpoint_id,retrieved_at,request_params_json,payload_json,payload_sha256) VALUES ('raw-a1','run-a1','fixture_source','fixture_endpoint','2026-11-09T10:00:00Z','{}','{}','fixture-hash')")
        register_snapshot_for_records(self.conn, snapshot_id="snap-a1", event_id="snap-a1:registered",
            effective_at="2026-11-09T10:02:30Z", actor_id="test-governance", reason="publish approved fixture snapshot", records=[record])
        grant = authorize_snapshot(self.conn, snapshot_id="snap-a1", records=[record],
            expected_snapshot_hash=snapshot_content_hash([record]),
            expected_assessments_hash=decision.assessments_hash,
            allowed_policy_versions={decision.policy_version}, now="2026-11-09T10:03:00Z")
        self.assertEqual(grant.record_ids, (record.record_id,))


    def test_a13_read_gate_requires_exact_record_raw_response_and_source_index_links(self):
        self.conn.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('fixture_source','Fixture','api')")
        self.conn.execute("INSERT INTO collectors(collector_id,collector_name,module_code,version) VALUES ('fixture-collector','Fixture','A1','1')")
        self.conn.execute("""INSERT INTO collection_runs(run_id,collector_id,source_id,collector_version,requested_scope_json,started_at,status)
            VALUES ('run-coordinate','fixture-collector','fixture_source','1','{}','2026-11-09T10:00:00Z','completed')""")
        self.conn.execute("""INSERT INTO raw_responses(raw_response_id,run_id,source_id,endpoint_id,retrieved_at,request_params_json,payload_json,payload_sha256)
            VALUES ('raw-coordinate','run-coordinate','fixture_source','fixture_endpoint','2026-11-09T10:00:00Z','{}','{}','fixture-hash')""")
        ctx = DiscoveryContext("solana", MINT, "fixture_source", "fixture_endpoint",
                               "run-coordinate", "raw-coordinate", 4, "2026-11-09T10:00:00Z")
        record = project_a1(normalize_a1(ctx, {"symbol":"SOL"}, field_map={"symbol":"symbol"}))
        _, decision = assess_and_store_snapshot(self.conn, [record], snapshot_id="snap-coordinate-links",
            source_policies=POLICIES, assessed_at="2026-11-09T10:01:00Z",
            decided_at="2026-11-09T10:02:00Z")
        from rooibos_a13.lifecycle import register_snapshot_for_records
        register_snapshot_for_records(self.conn, snapshot_id="snap-coordinate-links",
            event_id="snap-coordinate-links:registered", effective_at="2026-11-09T10:02:30Z",
            actor_id="test-governance", reason="register exact source-coordinate links", records=[record])
        links = self.conn.execute(
            "SELECT record_id,raw_response_id,source_record_index FROM a13_snapshot_evidence_links "
            "WHERE snapshot_id=?", ("snap-coordinate-links",)).fetchall()
        self.assertEqual([tuple(row) for row in links],
                         [(record.record_id, "raw-coordinate", 4)])
        kwargs = dict(snapshot_id="snap-coordinate-links", records=[record],
            expected_snapshot_hash=snapshot_content_hash([record]),
            expected_assessments_hash=decision.assessments_hash,
            allowed_policy_versions={decision.policy_version}, now="2026-11-09T10:03:00Z")
        self.assertEqual(authorize_snapshot(self.conn, **kwargs).record_ids, (record.record_id,))

        # An extra raw-coordinate mapping must deny access even when the
        # snapshot-level set of raw response IDs remains unchanged.
        self.conn.execute(
            "INSERT INTO a13_snapshot_evidence_links "
            "(snapshot_id,record_id,raw_response_id,source_record_index,linked_at) "
            "VALUES (?,?,?,?,?)",
            ("snap-coordinate-links", "unexpected-record", "raw-coordinate", 99,
             "2026-11-09T10:02:30Z"))
        with self.assertRaisesRegex(SnapshotAccessDenied, "record-to-raw-response/source-index"):
            authorize_snapshot(self.conn, **kwargs)

        # Simulate corruption/missing evidence in an isolated test database:
        # remove the append-only guard, then prove the read gate fails closed.
        self.conn.execute("DROP TRIGGER trg_a13_evidence_links_no_delete")
        self.conn.execute(
            "DELETE FROM a13_snapshot_evidence_links WHERE snapshot_id=? AND record_id=?",
            ("snap-coordinate-links", record.record_id))
        with self.assertRaisesRegex(SnapshotAccessDenied, "record-to-raw-response/source-index"):
            authorize_snapshot(self.conn, **kwargs)

    def test_a1_storage_requires_matching_raw_response_provenance_and_exact_replay(self):
        self.conn.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('fixture_source','Fixture','api')")
        self.conn.execute("INSERT INTO source_endpoints(endpoint_id,source_id,endpoint_name,path) VALUES ('fixture_endpoint','fixture_source','fixture','/fixture')")
        self.conn.execute("INSERT INTO collectors(collector_id,collector_name,module_code,version) VALUES ('fixture-a1','A1','A1','1')")
        self.conn.execute("""INSERT INTO collection_runs(run_id,collector_id,source_id,endpoint_id,collector_version,requested_scope_json,started_at,status)
            VALUES ('run-a1-store','fixture-a1','fixture_source','fixture_endpoint','1','{}','2026-11-09T10:00:00Z','raw_captured')""")
        self.conn.execute("""INSERT INTO raw_responses(raw_response_id,run_id,source_id,endpoint_id,retrieved_at,request_params_json,payload_json,payload_sha256)
            VALUES ('raw-a1-store','run-a1-store','fixture_source','fixture_endpoint','2026-11-09T10:00:01Z','{}','{}','fixture-hash')""")
        ctx = DiscoveryContext('solana', MINT, 'fixture_source', 'fixture_endpoint', 'run-a1-store', 'raw-a1-store', 0, '2026-11-09T10:00:01Z')
        obs = normalize_a1(ctx, {'symbol':'SOL'}, field_map={'symbol':'symbol'})
        self.assertTrue(store_a1_observation(self.conn, obs))
        self.assertFalse(store_a1_observation(self.conn, obs))
        bad_ctx = DiscoveryContext('solana', MINT, 'other_source', 'fixture_endpoint', 'run-a1-store', 'raw-a1-store', 0, '2026-11-09T10:00:01Z')
        bad = normalize_a1(bad_ctx, {'symbol':'SOL'}, field_map={'symbol':'symbol'})
        with self.assertRaises(ValueError):
            store_a1_observation(self.conn, bad)
        count = self.conn.execute('SELECT COUNT(*) FROM token_discovery_observations').fetchone()[0]
        self.assertEqual(count, 1)

    def test_a2_numeric_text_and_explicit_missingness_projection(self):
        base = dict(chain="solana", mint_address=MINT, observed_at="2026-11-09T10:00:00Z",
                    source_id="fixture_source", endpoint_id="fixture_endpoint", run_id="run-a2",
                    raw_response_id="raw-a2", source_record_index=0)
        item = MarketObservationInput(**base, metrics=(
            MarketMetricInput("price", 1.25, unit="quote_per_token"),
            MarketMetricInput("market_name", "Pool Alpha", unit="text"),
            MarketMetricInput("liquidity", None, value_status="unavailable", unit="currency"),
        ))
        result = normalize_a2(item)
        self.assertEqual(result.rejected_metric_count, 0)
        records = [project_a2(x) for x in result.observations]
        assessments, decision = assess_and_store_snapshot(self.conn, records, snapshot_id="snap-a2",
            source_policies=POLICIES, assessed_at="2026-11-09T10:01:00Z", decided_at="2026-11-09T10:02:00Z")
        self.assertEqual(decision.dataset_status, "approved_for_research")

    def test_a6_projection_unconfirmed_is_not_promoted_to_closed(self):
        context = ParseContext("solana", "asset-1", MINT, "15m", "fixture_source",
            "fixture_endpoint", "run-a6", "raw-a6", volume_unit="base_token")
        parsed = normalise_records([{"timestamp_ms": 1794229200000, "open": 1.0, "high": 1.2,
            "low": 0.9, "close": 1.1, "volume": 12.0}], context)
        record = project_a6(parsed.candles[0], observed_at="2026-11-09T10:00:00Z")
        self.assertEqual(record.payload["candle_status"], "unknown")
        assessments, decision = assess_and_store_snapshot(self.conn, [record], snapshot_id="snap-a6",
            source_policies=POLICIES, assessed_at="2026-11-09T10:01:00Z", decided_at="2026-11-09T10:02:00Z")
        self.assertEqual(decision.dataset_status, "quarantined")
        with self.assertRaises(SnapshotAccessDenied):
            authorize_snapshot(self.conn, snapshot_id="snap-a6", records=[record],
                expected_snapshot_hash=snapshot_content_hash([record]),
                expected_assessments_hash=decision.assessments_hash,
                allowed_policy_versions={decision.policy_version}, now="2026-11-09T10:03:00Z")

    def test_a13_warnings_are_quarantined_unless_explicitly_allowed(self):
        context = ParseContext("solana", "asset-warning", MINT, "15m",
            "fixture_source", "fixture_endpoint", "run-warning", "raw-warning",
            volume_unit="base_token")
        parsed = normalise_records([{"timestamp_ms":1794229200000,"open":1.0,
            "high":1.2,"low":0.9,"close":1.1,"volume":12.0}], context)
        record = project_a6(parsed.candles[0], observed_at="2026-11-09T10:00:00Z")
        assessments, denied = assess_and_store_snapshot(self.conn, [record],
            snapshot_id="snap-warning-default", source_policies=POLICIES,
            assessed_at="2026-11-09T10:01:00Z", decided_at="2026-11-09T10:02:00Z")
        self.assertEqual(assessments[0].status, "valid_with_warnings")
        self.assertEqual(denied.dataset_status, "quarantined")
        _, allowed = assess_and_store_snapshot(self.conn, [record],
            snapshot_id="snap-warning-allowed", source_policies=POLICIES,
            assessed_at="2026-11-09T10:01:00Z", decided_at="2026-11-09T10:02:00Z",
            allow_warnings=True)
        self.assertEqual(allowed.dataset_status, "approved_with_warnings")

    def test_a13_incomplete_expected_membership_is_quarantined(self):
        ctx = DiscoveryContext("solana", MINT, "fixture_source", "fixture_endpoint",
            "run-incomplete", "raw-incomplete", 0, "2026-11-09T10:00:00Z")
        record = project_a1(normalize_a1(ctx, {"symbol":"SOL"},
            field_map={"symbol":"symbol"}))
        _, decision = assess_and_store_snapshot(self.conn, [record],
            snapshot_id="snap-incomplete", source_policies=POLICIES,
            assessed_at="2026-11-09T10:01:00Z", decided_at="2026-11-09T10:02:00Z",
            expected_record_ids={record.record_id, "expected-but-missing"})
        self.assertEqual(decision.dataset_status, "quarantined")
        self.assertIn("snapshot_incomplete", {finding.code for finding in decision.findings})

    def test_a13_assessments_and_decision_roll_back_as_one_unit(self):
        ctx = DiscoveryContext("solana", MINT, "fixture_source", "fixture_endpoint",
            "run-atomic", "raw-atomic", 7, "2026-11-09T10:00:00Z")
        record = project_a1(normalize_a1(ctx, {"symbol":"SOL"},
            field_map={"symbol":"symbol"}))
        # Force the immutable decision insert to fail after assessment staging.
        self.conn.execute("""INSERT INTO a13_snapshot_decisions
            (snapshot_id,dataset_type,record_ids_json,dataset_status,assessments_hash,
             findings_json,decided_at,policy_version,snapshot_content_hash)
            VALUES ('snap-atomic','a1_discovery','["different-record"]','approved_for_research',
                    'old-assessments','[]','2026-11-09T10:00:00Z',
                    'a13-governance/0.1.0','old-snapshot-hash')""")
        before = self.conn.execute(
            "SELECT COUNT(*) FROM a13_record_assessments").fetchone()[0]
        with self.assertRaises(ValueError):
            assess_and_store_snapshot(self.conn, [record], snapshot_id="snap-atomic",
                source_policies=POLICIES, assessed_at="2026-11-09T10:01:00Z",
                decided_at="2026-11-09T10:02:00Z")
        after = self.conn.execute(
            "SELECT COUNT(*) FROM a13_record_assessments").fetchone()[0]
        self.assertEqual(after, before)
        self.assertEqual(self.conn.execute(
            "SELECT record_ids_json FROM a13_snapshot_decisions WHERE snapshot_id='snap-atomic'"
        ).fetchone()[0], '["different-record"]')

    def test_read_gate_rejects_altered_record_membership(self):
        ctx = DiscoveryContext("solana", MINT, "fixture_source", "fixture_endpoint", "run-a1",
                               "raw-a1", 0, "2026-11-09T10:00:00Z")
        obs = normalize_a1(ctx, {"symbol":"SOL"}, field_map={"symbol":"symbol"})
        record = project_a1(obs)
        _, decision = assess_and_store_snapshot(self.conn, [record], snapshot_id="snap-gate",
            source_policies=POLICIES, assessed_at="2026-11-09T10:01:00Z", decided_at="2026-11-09T10:02:00Z")
        from dataclasses import replace
        altered = replace(record, payload={"chain":"solana", "mint":MINT, "symbol":"CHANGED"},
                          content_hash="0" * 64)
        with self.assertRaises(SnapshotAccessDenied):
            authorize_snapshot(self.conn, snapshot_id="snap-gate", records=[altered],
                expected_snapshot_hash=snapshot_content_hash([altered]),
                expected_assessments_hash=decision.assessments_hash,
                allowed_policy_versions={decision.policy_version}, now="2026-11-09T10:03:00Z")


    def test_read_gate_hash_binds_raw_response_to_source_record_index(self):
        ctx = DiscoveryContext("solana", MINT, "fixture_source", "fixture_endpoint",
                               "run-coordinate", "raw-coordinate", 4, "2026-11-09T10:00:00Z")
        record = project_a1(normalize_a1(ctx, {"symbol":"SOL"}, field_map={"symbol":"symbol"}))
        _, decision = assess_and_store_snapshot(self.conn, [record], snapshot_id="snap-coordinate",
            source_policies=POLICIES, assessed_at="2026-11-09T10:01:00Z",
            decided_at="2026-11-09T10:02:00Z")
        from dataclasses import replace
        altered = replace(record, source_record_index=5)
        # Even when the caller recomputes the supplied hash, the persisted
        # approval remains bound to the original raw-response/source-index pair.
        with self.assertRaises(SnapshotAccessDenied):
            authorize_snapshot(self.conn, snapshot_id="snap-coordinate", records=[altered],
                expected_snapshot_hash=snapshot_content_hash([altered]),
                expected_assessments_hash=decision.assessments_hash,
                allowed_policy_versions={decision.policy_version}, now="2026-11-09T10:03:00Z")

if __name__ == "__main__":
    unittest.main()
