"""A6 quarantine and replay acceptance tests; synthetic fixtures only."""
from test_a6_pipeline_e2e import A6CollectorToA13E2ETests
from rooibos_collectors.a6_pipeline import process_a6_raw_response


class A6QuarantineAndReplayTests(A6CollectorToA13E2ETests):
    def _process(self, raw_id, snapshot_id, decoder):
        run_id, _ = self._capture(raw_id)
        result = process_a6_raw_response(
            self.framework, run_id=run_id, raw_response_id=raw_id,
            parse_context=self._context(run_id, raw_id),
            request_identity_fields={"mint_address": "mint", "interval": "interval"},
            response_identity_fields={"mint_address": ("mint",)},
            decode_payload=decoder, snapshot_id=snapshot_id,
            source_policies=POLICIES,
            assessed_at="2026-11-11T10:01:00Z",
            decided_at="2026-11-11T10:02:00Z",
            effective_at="2026-11-11T10:03:00Z",
            actor_id="synthetic-test", reason="A6 quarantine/replay synthetic acceptance",
        )
        return run_id, result

    def _assert_not_published(self, run_id, snapshot_id, expected_status):
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], expected_status)
            for table in (
                "a13_record_assessments", "a13_snapshot_decisions",
                "a13_snapshot_lifecycle_events", "a13_snapshot_evidence_links",
                "a13_snapshot_raw_links",
            ):
                count = conn.execute(
                    f"SELECT COUNT(*) FROM {table}" +
                    (" WHERE snapshot_id=?" if table != "a13_record_assessments" else ""),
                    (snapshot_id,) if table != "a13_record_assessments" else (),
                ).fetchone()[0]
                self.assertEqual(count, 0, table)
        finally:
            conn.close()

    def test_parser_conflicting_candle_variants_quarantine_without_a13_publication(self):
        def conflicting(payload):
            rows = self._decode(payload)
            rows[1]["timestamp_ms"] = rows[0]["timestamp_ms"]
            rows[1]["close"] = 1.15
            rows[1]["high"] = 1.25
            return rows

        run_id, result = self._process("raw-a6-parser-conflict", "snap-a6-parser-conflict", conflicting)
        self.assertEqual(result.parse_result.conflict_count, 1)
        self.assertIsNone(result.grant)
        self._assert_not_published(run_id, "snap-a6-parser-conflict", "quarantined")

    def test_rejected_record_quarantines_valid_subset_without_a13_publication(self):
        def one_rejected(payload):
            rows = self._decode(payload)
            rows[1]["high"] = 0.5
            return rows

        run_id, result = self._process("raw-a6-rejected", "snap-a6-rejected", one_rejected)
        self.assertEqual(result.parse_result.rejected_count, 1)
        self.assertEqual(len(result.candle_ids), 1)
        self.assertIsNone(result.grant)
        self._assert_not_published(run_id, "snap-a6-rejected", "quarantined")

    def test_all_records_rejected_quarantines_empty_candle_output(self):
        def all_rejected(payload):
            rows = self._decode(payload)
            for row in rows:
                row["volume"] = -1
            return rows

        run_id, result = self._process("raw-a6-empty", "snap-a6-empty", all_rejected)
        self.assertEqual(result.parse_result.rejected_count, 2)
        self.assertEqual(result.candle_ids, ())
        self.assertIsNone(result.grant)
        self._assert_not_published(run_id, "snap-a6-empty", "quarantined")

    def test_duplicate_source_indexes_fail_closed_before_any_publication(self):
        def duplicate_index(payload):
            rows = self._decode(payload)
            rows[1]["source_record_index"] = rows[0]["source_record_index"]
            return rows

        run_id, _ = self._capture("raw-a6-duplicate-index")
        with self.assertRaisesRegex(ValueError, "duplicate original source_record_index"):
            process_a6_raw_response(
                self.framework, run_id=run_id, raw_response_id="raw-a6-duplicate-index",
                parse_context=self._context(run_id, "raw-a6-duplicate-index"),
                request_identity_fields={"mint_address": "mint", "interval": "interval"},
                response_identity_fields={"mint_address": ("mint",)},
                decode_payload=duplicate_index, snapshot_id="snap-a6-duplicate-index",
                source_policies=POLICIES,
                assessed_at="2026-11-11T10:01:00Z",
                decided_at="2026-11-11T10:02:00Z",
                effective_at="2026-11-11T10:03:00Z",
                actor_id="synthetic-test", reason="duplicate source index regression",
            )
        self._assert_not_published(run_id, "snap-a6-duplicate-index", "failed")

    def test_reprocessing_completed_response_is_rejected_without_duplicate_publication(self):
        run_id, first = self._run("raw-a6-replay", "snap-a6-replay")
        self.assertTrue(first.published)
        with self.assertRaisesRegex(ValueError, "raw_captured state"):
            process_a6_raw_response(
                self.framework, run_id=run_id, raw_response_id="raw-a6-replay",
                parse_context=self._context(run_id, "raw-a6-replay"),
                request_identity_fields={"mint_address": "mint", "interval": "interval"},
                response_identity_fields={"mint_address": ("mint",)},
                decode_payload=self._decode, snapshot_id="snap-a6-replay-second",
                source_policies=POLICIES,
                assessed_at="2026-11-11T10:04:00Z",
                decided_at="2026-11-11T10:05:00Z",
                effective_at="2026-11-11T10:06:00Z",
                actor_id="synthetic-test", reason="replay rejection regression",
            )
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "completed")
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM a13_snapshot_decisions"
            ).fetchone()[0], 1)
        finally:
            conn.close()



    def test_multiple_captured_responses_in_one_run_are_rejected_before_parsing(self):
        run_id, payload = self._capture("raw-a6-multi-1")
        second_ticket = self.framework.authorise_request(run_id, {"mint": MINT, "interval": "15m"})
        self.framework.capture_response(
            second_ticket, raw_response_id="raw-a6-multi-2", payload=payload,
            http_status=200, retrieved_at="2026-11-11T10:00:02Z", page_sequence=1,
        )
        with self.assertRaisesRegex(ValueError, "multi-response aggregation is not implemented"):
            process_a6_raw_response(
                self.framework, run_id=run_id, raw_response_id="raw-a6-multi-1",
                parse_context=self._context(run_id, "raw-a6-multi-1"),
                request_identity_fields={"mint_address": "mint", "interval": "interval"},
                response_identity_fields={"mint_address": ("mint",)},
                decode_payload=self._decode, snapshot_id="snap-a6-multi",
                source_policies=POLICIES,
                assessed_at="2026-11-11T10:01:00Z",
                decided_at="2026-11-11T10:02:00Z",
                effective_at="2026-11-11T10:03:00Z",
                actor_id="synthetic-test", reason="multi-response fail-closed regression",
            )
        conn = _connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM raw_responses WHERE run_id=?", (run_id,)
            ).fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ohlcv_candles").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM a13_snapshot_decisions").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT status FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()[0], "raw_captured")
        finally:
            conn.close()


# Reuse the fixture policy declared by the end-to-end acceptance suite.
from test_a6_pipeline_e2e import POLICIES
from rooibos_db.database import _connect
