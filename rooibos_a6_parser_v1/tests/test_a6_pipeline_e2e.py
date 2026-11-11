"""Synthetic A6 collector-to-A13 end-to-end acceptance tests.

No network calls or live data: the tests exercise only captured synthetic
responses, persistence, provenance, A13 publication and completion gating.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rooibos_db.database import initialize_database, _connect
from rooibos_collectors import CollectorFramework, EndpointPolicy
from rooibos_collectors.a6_pipeline import process_a6_raw_response
from rooibos_collectors.publication_recovery import recover_a13_publication
from rooibos_a6.parser import ParseContext
from rooibos_a13.models import SourcePolicy
from rooibos_integrations.a13_projection import publish_a6_snapshot_from_storage as real_publish

MINT = "So11111111111111111111111111111111111111112"
POLICIES = {"fixture_source": SourcePolicy("fixture_source", ("fixture_endpoint",), True)}


class A6CollectorToA13E2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "moby_market.db"
        initialize_database(self.db)
        conn = _connect(self.db)
        try:
            conn.execute("INSERT INTO sources(source_id,source_name,source_type) VALUES ('fixture_source','Fixture','api')")
            conn.execute("""INSERT INTO source_endpoints(endpoint_id,source_id,endpoint_name,method,path)
                VALUES ('fixture_endpoint','fixture_source','fixture_read','GET','/fixture/read')""")
            conn.execute("""INSERT INTO collectors(collector_id,collector_name,module_code,version)
                VALUES ('A6','A6 Fixture','A6','1.0.0')""")
            conn.execute("INSERT INTO assets(asset_id,chain,token_address,symbol) VALUES ('asset-fixture','solana',?,'FIX')", (MINT,))
            conn.commit()
        finally:
            conn.close()
        policy = EndpointPolicy(
            source_id="fixture_source", endpoint_id="fixture_endpoint",
            endpoint_name="fixture_read", method="GET", path="/fixture/read",
            allowed_parameter_keys=frozenset({"mint", "interval"}), validated=True,
        )
        self.framework = CollectorFramework(self.db, [policy])

    def tearDown(self):
        self.tmp.cleanup()

    def _capture(self, raw_id):
        run_id = "run-a6"
        self.framework.create_run(
            run_id=run_id, collector_id="A6", source_id="fixture_source",
            endpoint_id="fixture_endpoint", collector_version="1.0.0",
            requested_scope={"mint": MINT, "interval": "15m"},
            started_at="2026-11-11T10:00:00Z",
        )
        self.framework.transition(run_id, "configured")
        ticket = self.framework.authorise_request(run_id, {"mint": MINT, "interval": "15m"})
        payload = {
            "mint": MINT,
            "records": [
                {"timestamp_ms": 1794225600000, "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 10.0},
                {"timestamp_ms": 1794226500000, "open": 1.1, "high": 1.3, "low": 1.0, "close": 1.2, "volume": 12.0},
            ],
        }
        self.framework.capture_response(
            ticket, raw_response_id=raw_id, payload=payload, http_status=200,
            retrieved_at="2026-11-11T10:00:01Z", page_sequence=0,
        )
        return run_id, payload

    def _context(self, run_id, raw_id):
        return ParseContext(
            chain="solana", asset_id="asset-fixture", mint_address=MINT,
            interval="15m", source_id="fixture_source",
            endpoint_id="fixture_endpoint", collection_run_id=run_id,
            raw_response_ref=raw_id, volume_unit="base_token",
        )

    @staticmethod
    def _decode(payload):
        return [
            dict(row, source_record_index=index, source_record_count=len(payload["records"]),
                 candle_status="closed")
            for index, row in enumerate(payload["records"])
        ]

    def _run(self, raw_id, snapshot_id):
        run_id, _ = self._capture(raw_id)
        return run_id, process_a6_raw_response(
            self.framework, run_id=run_id, raw_response_id=raw_id,
            parse_context=self._context(run_id, raw_id),
            request_identity_fields={"mint_address": "mint", "interval": "interval"},
            response_identity_fields={"mint_address": ("mint",)},
            decode_payload=self._decode, snapshot_id=snapshot_id,
            source_policies=POLICIES,
            assessed_at="2026-11-11T10:01:00Z",
            decided_at="2026-11-11T10:02:00Z",
            effective_at="2026-11-11T10:03:00Z",
            actor_id="synthetic-test", reason="A6 end-to-end synthetic acceptance",
        )

    def test_a6_capture_decode_persist_assess_publish_and_authorize(self):
        run_id, result = self._run("raw-a6-success", "snap-a6-success")
        self.assertTrue(result.published)
        self.assertEqual(result.decision.dataset_status, "approved_for_research")
        self.assertEqual(len(result.candle_ids), 2)
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "completed")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_evidence_links WHERE snapshot_id='snap-a6-success'"
            ).fetchone()[0], 2)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_raw_links WHERE snapshot_id='snap-a6-success'"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a6_candle_evidence_links WHERE raw_response_id='raw-a6-success'"
            ).fetchone()[0], 2)
        finally:
            conn.close()

    def test_a6_publication_only_recovery_does_not_repeat_normalization(self):
        raw_id = "raw-a6-publication-recovery"
        run_id, payload = self._capture(raw_id)
        context = self._context(run_id, raw_id)
        kwargs = dict(
            parse_context=context,
            request_identity_fields={"mint_address": "mint", "interval": "interval"},
            response_identity_fields={"mint_address": ("mint",)},
            decode_payload=self._decode, snapshot_id="snap-a6-recovery",
            source_policies=POLICIES,
            assessed_at="2026-11-11T10:01:00Z",
            decided_at="2026-11-11T10:02:00Z",
            effective_at="2026-11-11T10:03:00Z",
            actor_id="synthetic-test", reason="A6 publication recovery acceptance",
        )
        with patch("rooibos_collectors.a6_pipeline.publish_a6_snapshot_from_storage",
                   side_effect=RuntimeError("synthetic A13 publication outage")):
            with self.assertRaisesRegex(RuntimeError, "publication outage"):
                process_a6_raw_response(
                    self.framework, run_id=run_id, raw_response_id=raw_id, **kwargs,
                )

        conn = _connect(self.db)
        try:
            before = {
                "candles": conn.execute(
                    "SELECT COUNT(*) FROM ohlcv_candles WHERE run_id=?", (run_id,)
                ).fetchone()[0],
                "links": conn.execute(
                    "SELECT COUNT(*) FROM a6_candle_evidence_links WHERE run_id=?", (run_id,)
                ).fetchone()[0],
                "events": conn.execute(
                    "SELECT COUNT(*) FROM normalization_events WHERE run_id=?", (run_id,)
                ).fetchone()[0],
            }
            self.assertEqual(before, {"candles": 2, "links": 2, "events": 1})
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "validating")
            self.assertIsNotNone(conn.execute(
                "SELECT publication_failed_at FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0])
        finally:
            conn.close()

        recovered = recover_a13_publication(
            self.framework, run_id=run_id, raw_response_id=raw_id,
            snapshot_id="snap-a6-recovery", source_policies=POLICIES,
            assessed_at=kwargs["assessed_at"], decided_at=kwargs["decided_at"],
            effective_at=kwargs["effective_at"], actor_id=kwargs["actor_id"],
            reason=kwargs["reason"],
        )
        self.assertTrue(recovered["published"])
        conn = _connect(self.db)
        try:
            after = {
                "candles": conn.execute(
                    "SELECT COUNT(*) FROM ohlcv_candles WHERE run_id=?", (run_id,)
                ).fetchone()[0],
                "links": conn.execute(
                    "SELECT COUNT(*) FROM a6_candle_evidence_links WHERE run_id=?", (run_id,)
                ).fetchone()[0],
                "events": conn.execute(
                    "SELECT COUNT(*) FROM normalization_events WHERE run_id=?", (run_id,)
                ).fetchone()[0],
            }
            self.assertEqual(after, before)
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "completed")
        finally:
            conn.close()

    def test_a6_publication_and_completion_share_one_rollback_boundary(self):
        run_id, _ = self._capture("raw-a6-atomic")
        context = self._context(run_id, "raw-a6-atomic")

        def publish_then_invalidate(conn, *args, **kwargs):
            self.assertTrue(conn.in_transaction)
            result = real_publish(conn, *args, **kwargs)
            conn.execute(
                "UPDATE collection_runs SET status='cancelled' WHERE run_id=?", (run_id,)
            )
            return result

        with patch("rooibos_collectors.a6_pipeline.publish_a6_snapshot_from_storage",
                   side_effect=publish_then_invalidate):
            with self.assertRaisesRegex(Exception, "validating"):
                process_a6_raw_response(
                    self.framework, run_id=run_id, raw_response_id="raw-a6-atomic",
                    parse_context=context,
                    request_identity_fields={"mint_address": "mint", "interval": "interval"},
                    response_identity_fields={"mint_address": ("mint",)},
                    decode_payload=self._decode, snapshot_id="snap-a6-atomic",
                    source_policies=POLICIES,
                    assessed_at="2026-11-11T10:01:00Z",
                    decided_at="2026-11-11T10:02:00Z",
                    effective_at="2026-11-11T10:03:00Z",
                    actor_id="synthetic-test", reason="A6 atomic rollback regression",
                )

        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "validating")
            self.assertIsNotNone(conn.execute(
                "SELECT publication_failed_at FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0])
            for table in (
                "a13_record_assessments", "a13_snapshot_decisions",
                "a13_snapshot_lifecycle_events", "a13_snapshot_evidence_links",
                "a13_snapshot_raw_links",
            ):
                self.assertEqual(conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE snapshot_id=?"
                    if table != "a13_record_assessments"
                    else "SELECT COUNT(*) FROM a13_record_assessments",
                    ("snap-a6-atomic",) if table != "a13_record_assessments" else (),
                ).fetchone()[0], 0, table)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
