"""Shared-database synthetic rehearsal for A1, A2, A6 and their A13 boundary.

The test runs all three collectors sequentially against one initialized database.
It verifies that collector registrations, raw evidence, normalized records and
A13 snapshot links remain attributable to their own run/module.
"""
import tempfile
import unittest
from pathlib import Path

from rooibos_db.database import initialize_database, _connect
from rooibos_collectors import CollectorFramework, EndpointPolicy
from rooibos_collectors.a1_pipeline import process_a1_raw_response
from rooibos_collectors.a2_pipeline import process_a2_raw_response
from rooibos_collectors.a6_pipeline import process_a6_raw_response
from rooibos_a1.models import DiscoveryContext, normalize_observation as normalize_a1
from rooibos_a2.normalizer import MarketMetricInput, MarketObservationInput
from rooibos_a6.parser import ParseContext
from rooibos_a13.models import SourcePolicy

MINT = "So11111111111111111111111111111111111111112"
SOURCE_POLICIES = {
    "fixture_source": SourcePolicy("fixture_source", ("fixture_endpoint",), True)
}
TIMES = {
    "assessed_at": "2026-11-11T11:01:00Z",
    "decided_at": "2026-11-11T11:02:00Z",
    "effective_at": "2026-11-11T11:03:00Z",
    "actor_id": "shared-db-synthetic-test",
    "reason": "sequential shared-database integration rehearsal",
}


class SharedCollectorDatabaseRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "moby_market.db"
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            conn.execute(
                "INSERT INTO sources(source_id,source_name,source_type) VALUES "
                "('fixture_source','Fixture','api')"
            )
            conn.execute(
                """INSERT INTO source_endpoints(
                    endpoint_id,source_id,endpoint_name,method,path
                ) VALUES ('fixture_endpoint','fixture_source','fixture_read','GET','/fixture/read')"""
            )
            for module in ("A1", "A2", "A6"):
                conn.execute(
                    """INSERT INTO collectors(collector_id,collector_name,module_code,version)
                    VALUES (?,?,?,'1.0.0')""",
                    (module, module + " fixture", module),
                )
            conn.execute(
                "INSERT INTO assets(asset_id,chain,token_address,symbol) "
                "VALUES ('asset-fixture','solana',?,'FIX')", (MINT,)
            )
            conn.commit()
        finally:
            conn.close()

        self.framework = CollectorFramework(self.db, [EndpointPolicy(
            source_id="fixture_source",
            endpoint_id="fixture_endpoint",
            endpoint_name="fixture_read",
            method="GET",
            path="/fixture/read",
            allowed_parameter_keys=frozenset({"scope", "mint", "interval"}),
            validated=True,
        )])

    def tearDown(self):
        self.tmp.cleanup()

    def _capture(self, module, payload, raw_id, params):
        run_id = "shared-" + module.lower()
        self.framework.create_run(
            run_id=run_id, collector_id=module, source_id="fixture_source",
            endpoint_id="fixture_endpoint", collector_version="1.0.0",
            requested_scope={"module": module}, started_at="2026-11-11T11:00:00Z",
        )
        self.framework.transition(run_id, "configured")
        ticket = self.framework.authorise_request(run_id, params)
        self.framework.capture_response(
            ticket, raw_response_id=raw_id, payload=payload, http_status=200,
            retrieved_at="2026-11-11T11:00:01Z", page_sequence=0,
        )
        return run_id

    def test_a1_a2_a6_publish_sequentially_without_cross_linking(self):
        a1_raw = "shared-raw-a1"
        a1_run = self._capture(
            "A1", {"records": [{"symbol": "SHARED"}]}, a1_raw, {"scope": "discovery"}
        )

        def decode_a1(payload, provenance):
            return [normalize_a1(
                DiscoveryContext(
                    "solana", MINT, provenance.source_id, provenance.endpoint_id,
                    provenance.run_id, provenance.raw_response_id, index,
                    provenance.retrieved_at, source_record_count=len(payload["records"]),
                ),
                row, field_map={"symbol": "symbol"},
            ) for index, row in enumerate(payload["records"])]

        a1 = process_a1_raw_response(
            self.framework, run_id=a1_run, raw_response_id=a1_raw,
            decode_payload=decode_a1, snapshot_id="shared-snap-a1",
            source_policies=SOURCE_POLICIES, **TIMES,
        )
        self.assertTrue(a1.published)

        a2_raw = "shared-raw-a2"
        a2_run = self._capture(
            "A2", {"records": [{"liquidity": 1234.5}]}, a2_raw, {"scope": "market"}
        )

        def decode_a2(payload, provenance):
            return [MarketObservationInput(
                chain="solana", mint_address=MINT, observed_at=provenance.retrieved_at,
                source_id=provenance.source_id, endpoint_id=provenance.endpoint_id,
                run_id=provenance.run_id, raw_response_id=provenance.raw_response_id,
                source_record_index=index,
                metrics=(MarketMetricInput("liquidity", row["liquidity"]),),
                market_scope="token_aggregated",
                source_record_count=len(payload["records"]),
            ) for index, row in enumerate(payload["records"])]

        a2 = process_a2_raw_response(
            self.framework, run_id=a2_run, raw_response_id=a2_raw,
            decode_payload=decode_a2, snapshot_id="shared-snap-a2",
            source_policies=SOURCE_POLICIES, **TIMES,
        )
        self.assertTrue(a2.published)

        a6_raw = "shared-raw-a6"
        a6_payload = {
            "mint": MINT,
            "records": [
                {"timestamp_ms": 1794225600000, "open": 1.0, "high": 1.2,
                 "low": 0.9, "close": 1.1, "volume": 10.0},
                {"timestamp_ms": 1794226500000, "open": 1.1, "high": 1.3,
                 "low": 1.0, "close": 1.2, "volume": 12.0},
            ],
        }
        a6_run = self._capture(
            "A6", a6_payload, a6_raw, {"mint": MINT, "interval": "15m"}
        )
        a6_context = ParseContext(
            chain="solana", asset_id="asset-fixture", mint_address=MINT,
            interval="15m", source_id="fixture_source",
            endpoint_id="fixture_endpoint", collection_run_id=a6_run,
            raw_response_ref=a6_raw, volume_unit="base_token",
        )

        def decode_a6(payload):
            return [
                dict(row, source_record_index=index,
                     source_record_count=len(payload["records"]),
                     candle_status="closed")
                for index, row in enumerate(payload["records"])
            ]

        a6 = process_a6_raw_response(
            self.framework, run_id=a6_run, raw_response_id=a6_raw,
            parse_context=a6_context,
            request_identity_fields={"mint_address": "mint", "interval": "interval"},
            response_identity_fields={"mint_address": ("mint",)},
            decode_payload=decode_a6, snapshot_id="shared-snap-a6",
            source_policies=SOURCE_POLICIES, **TIMES,
        )
        self.assertTrue(a6.published)
        self.assertEqual(len(a6.candle_ids), 2)

        conn = _connect(self.db)
        try:
            expected_runs = {
                a1_run: ("A1", a1_raw, "shared-snap-a1"),
                a2_run: ("A2", a2_raw, "shared-snap-a2"),
                a6_run: ("A6", a6_raw, "shared-snap-a6"),
            }
            for run_id, (module, raw_id, snapshot_id) in expected_runs.items():
                row = conn.execute(
                    """SELECT cr.status, collectors.module_code, rr.raw_response_id
                    FROM collection_runs cr
                    JOIN collectors ON collectors.collector_id=cr.collector_id
                    JOIN raw_responses rr ON rr.run_id=cr.run_id
                    WHERE cr.run_id=?""", (run_id,)
                ).fetchone()
                self.assertEqual(tuple(row), ("completed", module, raw_id))
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM a13_snapshot_raw_links WHERE snapshot_id=?",
                    (snapshot_id,),
                ).fetchone()[0], 1)
                self.assertEqual(conn.execute(
                    """SELECT COUNT(*) FROM a13_snapshot_raw_links
                    WHERE snapshot_id=? AND raw_response_id=?""",
                    (snapshot_id, raw_id),
                ).fetchone()[0], 1)
                self.assertEqual(conn.execute(
                    """SELECT COUNT(*) FROM a13_snapshot_evidence_links
                    WHERE snapshot_id=? AND run_id=?""",
                    (snapshot_id, run_id),
                ).fetchone()[0] > 0, True)

            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM collection_runs WHERE status='completed'"
            ).fetchone()[0], 3)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM raw_responses WHERE run_id IN (?,?,?)",
                (a1_run, a2_run, a6_run),
            ).fetchone()[0], 3)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_decisions WHERE snapshot_id IN "
                "('shared-snap-a1','shared-snap-a2','shared-snap-a6')"
            ).fetchone()[0], 3)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
