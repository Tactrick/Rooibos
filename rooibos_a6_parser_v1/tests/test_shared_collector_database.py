"""Shared-database synthetic rehearsal for A1, A2, A6 and their A13 boundary.

The test runs all three collectors sequentially against one initialized database.
It verifies that collector registrations, raw evidence, normalized records and
A13 snapshot links remain attributable to their own run/module.
"""
import tempfile
import unittest
from pathlib import Path

from rooibos_db.database import initialize_database, _connect
from rooibos_collectors import CollectorFramework, EndpointPolicy, CollectorPolicyError
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

    def _capture(self, module, payload, raw_id, params, *, run_id=None):
        run_id = run_id or ("shared-" + module.lower())
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
                    """SELECT COUNT(*) FROM a13_snapshot_evidence_links l
                    JOIN raw_responses rr ON rr.raw_response_id=l.raw_response_id
                    WHERE l.snapshot_id=? AND rr.run_id=?""",
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


    def test_quarantined_a1_does_not_block_or_leak_into_a2_a6_publication(self):
        # The source is active and its endpoint is approved, but its policy
        # explicitly excludes A1 discovery. A1 must be retained and quarantined;
        # A2 and A6 remain eligible under the same shared source registration.
        mixed_policies = {
            "fixture_source": SourcePolicy(
                "fixture_source", ("fixture_endpoint",), True,
                allowed_dataset_types=("a2_market", "a6_ohlcv"),
            )
        }
        a1_raw = "mixed-raw-a1"
        a1_run = self._capture(
            "A1", {"records": [{"symbol": "WITHHELD"}]}, a1_raw,
            {"scope": "discovery"},
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
            decode_payload=decode_a1, snapshot_id="mixed-snap-a1",
            source_policies=mixed_policies, **TIMES,
        )
        self.assertFalse(a1.published)
        self.assertEqual(a1.decision.dataset_status, "quarantined")

        a2_raw = "mixed-raw-a2"
        a2_run = self._capture(
            "A2", {"records": [{"liquidity": 2345.0}]}, a2_raw,
            {"scope": "market"},
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
            decode_payload=decode_a2, snapshot_id="mixed-snap-a2",
            source_policies=mixed_policies, **TIMES,
        )
        self.assertTrue(a2.published)

        a6_raw = "mixed-raw-a6"
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
            decode_payload=decode_a6, snapshot_id="mixed-snap-a6",
            source_policies=mixed_policies, **TIMES,
        )
        self.assertTrue(a6.published)
        self.assertEqual(len(a6.candle_ids), 2)

        conn = _connect(self.db)
        try:
            statuses = {
                row["run_id"]: row["status"]
                for row in conn.execute(
                    "SELECT run_id,status FROM collection_runs WHERE run_id IN (?,?,?)",
                    (a1_run, a2_run, a6_run),
                )
            }
            self.assertEqual(statuses, {
                a1_run: "quarantined", a2_run: "completed", a6_run: "completed",
            })

            # A1's immutable response remains available for audit, but its
            # quarantined snapshot has no consumer-facing lifecycle/raw links.
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM raw_responses WHERE raw_response_id=? AND run_id=?",
                (a1_raw, a1_run),
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_decisions WHERE snapshot_id='mixed-snap-a1'",
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT dataset_status FROM a13_snapshot_decisions WHERE snapshot_id='mixed-snap-a1'",
            ).fetchone()[0], "quarantined")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_raw_links WHERE snapshot_id='mixed-snap-a1'",
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_evidence_links WHERE snapshot_id='mixed-snap-a1'",
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_lifecycle_events WHERE snapshot_id='mixed-snap-a1'",
            ).fetchone()[0], 0)

            # A2 and A6 each publish only their own raw response and evidence.
            for snapshot_id, run_id, raw_id in (
                ("mixed-snap-a2", a2_run, a2_raw),
                ("mixed-snap-a6", a6_run, a6_raw),
            ):
                raw_links = conn.execute(
                    "SELECT raw_response_id FROM a13_snapshot_raw_links WHERE snapshot_id=?",
                    (snapshot_id,),
                ).fetchall()
                self.assertEqual([row[0] for row in raw_links], [raw_id])
                evidence_runs = conn.execute(
                    """SELECT DISTINCT rr.run_id FROM a13_snapshot_evidence_links l
                       JOIN raw_responses rr ON rr.raw_response_id=l.raw_response_id
                       WHERE l.snapshot_id=?""", (snapshot_id,),
                ).fetchall()
                self.assertTrue(evidence_runs)
                self.assertEqual({row[0] for row in evidence_runs}, {run_id})
                self.assertEqual(conn.execute(
                    """SELECT COUNT(*) FROM a13_snapshot_raw_links
                       WHERE snapshot_id=? AND raw_response_id=?""",
                    (snapshot_id, a1_raw),
                ).fetchone()[0], 0)
                self.assertEqual(conn.execute(
                    """SELECT COUNT(*) FROM a13_snapshot_evidence_links
                       WHERE snapshot_id=? AND raw_response_id=?""",
                    (snapshot_id, a1_raw),
                ).fetchone()[0], 0)

            # The quarantined A1 response is never linked by any other snapshot.
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_raw_links WHERE raw_response_id=?",
                (a1_raw,),
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_evidence_links WHERE raw_response_id=?",
                (a1_raw,),
            ).fetchone()[0], 0)
        finally:
            conn.close()


    def test_disabled_endpoint_policy_rejects_run_creation(self):
        disabled = CollectorFramework(self.db, [EndpointPolicy(
            source_id="fixture_source", endpoint_id="fixture_endpoint",
            endpoint_name="fixture_read", method="GET", path="/fixture/read",
            allowed_parameter_keys=frozenset({"scope"}),
            validated=True, enabled=False,
        )])
        with self.assertRaisesRegex(CollectorPolicyError, "policy is disabled"):
            disabled.create_run(
                run_id="disabled-endpoint-run", collector_id="A1",
                source_id="fixture_source", endpoint_id="fixture_endpoint",
                collector_version="1.0.0", requested_scope={"module": "A1"},
            )
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM collection_runs WHERE run_id='disabled-endpoint-run'"
            ).fetchone()[0], 0)
        finally:
            conn.close()

    def test_fresh_database_deployment_rehearsal_quarantine_and_recovery(self):
        # setUp creates the fresh deployment database and framework. Verify
        # registrations before attempting any collection work.
        conn = _connect(self.db)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM collectors WHERE is_enabled=1").fetchone()[0],
                3,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM sources WHERE source_id='fixture_source' AND is_enabled=1").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM source_endpoints WHERE endpoint_id='fixture_endpoint' AND is_enabled=1").fetchone()[0],
                1,
            )
        finally:
            conn.close()

        # Default-deny and unvalidated endpoint policies fail before a run is
        # created. No endpoint execution/network capability exists here.
        deny_all = CollectorFramework(self.db, [])
        with self.assertRaisesRegex(CollectorPolicyError, "default deny"):
            deny_all.create_run(
                run_id="deployment-unlisted-endpoint", collector_id="A1",
                source_id="fixture_source", endpoint_id="fixture_endpoint",
                collector_version="1.0.0", requested_scope={"module": "A1"},
            )
        unvalidated = CollectorFramework(self.db, [EndpointPolicy(
            source_id="fixture_source", endpoint_id="fixture_endpoint",
            endpoint_name="fixture_read", method="GET", path="/fixture/read",
            allowed_parameter_keys=frozenset({"scope"}), validated=False,
        )])
        with self.assertRaisesRegex(CollectorPolicyError, "has not passed validation"):
            unvalidated.create_run(
                run_id="deployment-unvalidated-endpoint", collector_id="A1",
                source_id="fixture_source", endpoint_id="fixture_endpoint",
                collector_version="1.0.0", requested_scope={"module": "A1"},
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

        # First attempt: endpoint transport policy allows capture, but A13's
        # narrower dataset policy excludes A1. Raw response is retained and
        # the run is quarantined without consumer links.
        rejected_run = self._capture(
            "A1", {"records": [{"symbol": "RECOVERY"}]},
            "deployment-a1-rejected-raw", {"scope": "discovery"},
            run_id="deployment-a1-rejected",
        )
        rejected = process_a1_raw_response(
            self.framework, run_id=rejected_run,
            raw_response_id="deployment-a1-rejected-raw",
            decode_payload=decode_a1, snapshot_id="deployment-a1-rejected-snapshot",
            source_policies={
                "fixture_source": SourcePolicy(
                    "fixture_source", ("fixture_endpoint",), True,
                    allowed_dataset_types=("a2_market",),
                )
            },
            **TIMES,
        )
        self.assertFalse(rejected.published)
        self.assertEqual(rejected.decision.dataset_status, "quarantined")

        # Recovery is a new auditable run, not a mutation/replay of the
        # terminal quarantined run. Once policy is corrected, A1 publishes.
        recovery_run = self._capture(
            "A1", {"records": [{"symbol": "RECOVERED"}]},
            "deployment-a1-recovery-raw", {"scope": "discovery"},
            run_id="deployment-a1-recovery",
        )
        recovered = process_a1_raw_response(
            self.framework, run_id=recovery_run,
            raw_response_id="deployment-a1-recovery-raw",
            decode_payload=decode_a1, snapshot_id="deployment-a1-recovery-snapshot",
            source_policies=SOURCE_POLICIES, **TIMES,
        )
        self.assertTrue(recovered.published)
        self.assertEqual(recovered.decision.dataset_status, "approved_for_research")

        conn = _connect(self.db)
        try:
            statuses = dict(conn.execute(
                "SELECT run_id,status FROM collection_runs WHERE run_id IN (?,?)",
                (rejected_run, recovery_run),
            ).fetchall())
            self.assertEqual(statuses, {
                rejected_run: "quarantined",
                recovery_run: "completed",
            })
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM raw_responses WHERE raw_response_id IN (?,?)",
                ("deployment-a1-rejected-raw", "deployment-a1-recovery-raw"),
            ).fetchone()[0], 2)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_raw_links WHERE snapshot_id='deployment-a1-rejected-snapshot'",
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_evidence_links WHERE snapshot_id='deployment-a1-rejected-snapshot'",
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_lifecycle_events WHERE snapshot_id='deployment-a1-rejected-snapshot'",
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT raw_response_id FROM a13_snapshot_raw_links WHERE snapshot_id='deployment-a1-recovery-snapshot'",
            ).fetchone()[0], "deployment-a1-recovery-raw")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_evidence_links WHERE snapshot_id='deployment-a1-recovery-snapshot'",
            ).fetchone()[0], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
