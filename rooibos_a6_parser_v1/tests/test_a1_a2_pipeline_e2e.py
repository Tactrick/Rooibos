"""Synthetic end-to-end A1/A2 collector-to-A13 acceptance tests.

These tests exercise captured-response provenance, decoder contracts, persistence,
A13 assessment/publication and final run-state gating without network access.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from rooibos_db.database import initialize_database, _connect
from rooibos_collectors import CollectorFramework, EndpointPolicy
from rooibos_collectors.a1_pipeline import process_a1_raw_response
from rooibos_collectors.a2_pipeline import process_a2_raw_response
from rooibos_a1.models import DiscoveryContext, normalize_observation as normalize_a1
from rooibos_a2.normalizer import MarketMetricInput, MarketObservationInput
from rooibos_a13.models import SourcePolicy

MINT = "So11111111111111111111111111111111111111112"
POLICIES = {"fixture_source": SourcePolicy("fixture_source", ("fixture_endpoint",), True)}


class A1A2CollectorToA13E2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "moby_market.db"
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            conn.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('fixture_source','Fixture','api')")
            conn.execute("""INSERT INTO source_endpoints(endpoint_id,source_id,endpoint_name,method,path)
                VALUES ('fixture_endpoint','fixture_source','fixture_read','GET','/fixture/read')""")
            for collector_id, module in (("A1","A1"),("A2","A2")):
                conn.execute("""INSERT INTO collectors(collector_id,collector_name,module_code,version)
                    VALUES (?,?,?,'1.0.0')""", (collector_id, module, module))
            conn.commit()
        finally:
            conn.close()
        policy = EndpointPolicy(
            source_id="fixture_source", endpoint_id="fixture_endpoint",
            endpoint_name="fixture_read", method="GET", path="/fixture/read",
            allowed_parameter_keys=frozenset({"scope"}), validated=True,
        )
        self.framework = CollectorFramework(self.db, [policy])

    def tearDown(self):
        self.tmp.cleanup()

    def _capture(self, module, payload, raw_id):
        run_id = f"run-{module.lower()}"
        self.framework.create_run(
            run_id=run_id, collector_id=module, source_id="fixture_source",
            endpoint_id="fixture_endpoint", collector_version="1.0.0",
            requested_scope={"scope": module}, started_at="2026-11-11T10:00:00Z",
        )
        self.framework.transition(run_id, "configured")
        ticket = self.framework.authorise_request(run_id, {"scope": module})
        self.framework.capture_response(
            ticket, raw_response_id=raw_id, payload=payload, http_status=200,
            retrieved_at="2026-11-11T10:00:01Z", page_sequence=0,
        )
        return run_id

    def _times(self):
        return dict(
            assessed_at="2026-11-11T10:01:00Z",
            decided_at="2026-11-11T10:02:00Z",
            effective_at="2026-11-11T10:03:00Z",
            actor_id="synthetic-test", reason="end-to-end synthetic acceptance",
        )

    def test_a1_capture_decode_persist_assess_publish_and_authorize(self):
        raw_id = "raw-a1-e2e"
        run_id = self._capture("A1", {"records":[{"symbol":"TEST","name":"Test Token"}]}, raw_id)

        def decode(payload, provenance):
            return [normalize_a1(
                DiscoveryContext(
                    "solana", MINT, provenance.source_id, provenance.endpoint_id,
                    provenance.run_id, provenance.raw_response_id, index,
                    provenance.retrieved_at, source_record_count=len(payload["records"]),
                ),
                row, field_map={"symbol":"symbol", "name":"name"},
            ) for index, row in enumerate(payload["records"])]

        result = process_a1_raw_response(
            self.framework, run_id=run_id, raw_response_id=raw_id,
            decode_payload=decode, snapshot_id="snap-a1-e2e",
            source_policies=POLICIES, **self._times(),
        )
        self.assertTrue(result.published)
        self.assertEqual(result.decision.dataset_status, "approved_for_research")
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "completed")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_evidence_links WHERE snapshot_id='snap-a1-e2e'"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_raw_links WHERE snapshot_id='snap-a1-e2e'"
            ).fetchone()[0], 1)
        finally:
            conn.close()

    def test_a2_capture_decode_metric_fanout_persist_assess_publish_and_authorize(self):
        raw_id = "raw-a2-e2e"
        run_id = self._capture("A2", {"records":[{"liquidity":1234.5}]}, raw_id)

        def decode(payload, provenance):
            return [MarketObservationInput(
                chain="solana", mint_address=MINT, observed_at=provenance.retrieved_at,
                source_id=provenance.source_id, endpoint_id=provenance.endpoint_id,
                run_id=provenance.run_id, raw_response_id=provenance.raw_response_id,
                source_record_index=index,
                metrics=(MarketMetricInput("liquidity", row["liquidity"]),),
                market_scope="token_aggregated", source_record_count=len(payload["records"]),
            ) for index, row in enumerate(payload["records"])]

        result = process_a2_raw_response(
            self.framework, run_id=run_id, raw_response_id=raw_id,
            decode_payload=decode, snapshot_id="snap-a2-e2e",
            source_policies=POLICIES, **self._times(),
        )
        self.assertTrue(result.published)
        self.assertEqual(result.decision.dataset_status, "approved_for_research")
        self.assertEqual(len(result.observation_ids), 1)
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "completed")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_evidence_links WHERE snapshot_id='snap-a2-e2e'"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_raw_links WHERE snapshot_id='snap-a2-e2e'"
            ).fetchone()[0], 1)
        finally:
            conn.close()

    def test_a1_incomplete_original_coverage_fails_before_persistence_or_publication(self):
        raw_id = "raw-a1-incomplete"
        run_id = self._capture("A1", {"records":[{"symbol":"A"},{"symbol":"B"}]}, raw_id)

        def decode(payload, provenance):
            return [normalize_a1(
                DiscoveryContext(
                    "solana", MINT, provenance.source_id, provenance.endpoint_id,
                    provenance.run_id, provenance.raw_response_id, 0,
                    provenance.retrieved_at, source_record_count=2,
                ), payload["records"][0], field_map={"symbol":"symbol"},
            )]

        with self.assertRaisesRegex(ValueError, "omitted or altered"):
            process_a1_raw_response(
                self.framework, run_id=run_id, raw_response_id=raw_id,
                decode_payload=decode, snapshot_id="snap-a1-incomplete",
                source_policies=POLICIES, **self._times(),
            )
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM token_discovery_observations"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "failed")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_decisions WHERE snapshot_id='snap-a1-incomplete'"
            ).fetchone()[0], 0)
        finally:
            conn.close()


    def test_a2_incomplete_original_coverage_fails_before_metric_persistence_or_publication(self):
        raw_id = "raw-a2-incomplete"
        run_id = self._capture("A2", {"records":[{"liquidity":1234.5},{"liquidity":987.0}]}, raw_id)

        def decode(payload, provenance):
            return [MarketObservationInput(
                chain="solana", mint_address=MINT, observed_at=provenance.retrieved_at,
                source_id=provenance.source_id, endpoint_id=provenance.endpoint_id,
                run_id=provenance.run_id, raw_response_id=provenance.raw_response_id,
                source_record_index=0,
                metrics=(MarketMetricInput("liquidity", payload["records"][0]["liquidity"]),),
                market_scope="token_aggregated", source_record_count=2,
            )]

        with self.assertRaisesRegex(ValueError, "omitted or altered"):
            process_a2_raw_response(
                self.framework, run_id=run_id, raw_response_id=raw_id,
                decode_payload=decode, snapshot_id="snap-a2-incomplete",
                source_policies=POLICIES, **self._times(),
            )
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM market_observations"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "failed")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_decisions WHERE snapshot_id='snap-a2-incomplete'"
            ).fetchone()[0], 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
