import unittest

from rooibos_a6 import ParseContext, ParserError, assess_completeness, normalise_records

BASE = 1_800_000_000_000


def context():
    return ParseContext(
        chain="solana", asset_id="asset-1", mint_address="mint-1", interval="15m",
        source_id="moby", endpoint_id="get_token_ohlcv", collection_run_id="run-1",
        raw_response_ref="raw-1", volume_unit="provider_unverified",
    )


def row(ts=BASE, **overrides):
    value = {"timestamp_ms": ts, "open": 10, "high": 12, "low": 9, "close": 11,
             "volume": 100, "candle_status": "closed"}
    value.update(overrides)
    return value


class ParserTests(unittest.TestCase):
    def test_valid_record_normalises(self):
        result = normalise_records([row()], context())
        self.assertEqual(len(result.candles), 1)
        self.assertEqual(result.candles[0].open_time_ms, BASE)
        self.assertEqual(result.candles[0].source_record_index, 0)
        self.assertEqual(result.rejected_count, 0)

    def test_unknown_provider_envelope_is_not_accepted_as_records(self):
        with self.assertRaises(ParserError):
            normalise_records([{"data": [row()]}], context())

    def test_missing_required_field_is_structural_failure(self):
        bad = row(); del bad["volume"]
        with self.assertRaises(ParserError):
            normalise_records([bad], context())

    def test_invalid_price_is_rejected(self):
        result = normalise_records([row(high=8)], context())
        self.assertEqual(result.rejected_count, 1)
        self.assertEqual(result.candles, [])

    def test_negative_volume_is_rejected(self):
        result = normalise_records([row(volume=-1)], context())
        self.assertEqual(result.rejected_count, 1)

    def test_null_numeric_is_rejected_not_coerced(self):
        result = normalise_records([row(close=None)], context())
        self.assertEqual(result.rejected_count, 1)

    def test_boolean_is_not_numeric(self):
        result = normalise_records([row(open=True)], context())
        self.assertEqual(result.rejected_count, 1)

    def test_non_finite_numeric_is_rejected(self):
        result = normalise_records([row(volume=float("nan"))], context())
        self.assertEqual(result.rejected_count, 1)

    def test_timestamp_must_be_integer_ms(self):
        result = normalise_records([row(timestamp_ms=str(BASE))], context())
        self.assertEqual(result.rejected_count, 1)

    def test_timestamp_alignment_is_checked(self):
        result = normalise_records([row(timestamp_ms=BASE + 1)], context())
        self.assertEqual(result.rejected_count, 1)

    def test_source_order_preserved_as_index_but_output_sorted(self):
        result = normalise_records([row(BASE + 900_000), row(BASE)], context())
        self.assertEqual([c.open_time_ms for c in result.candles], [BASE, BASE + 900_000])
        self.assertEqual([c.source_record_index for c in result.candles], [1, 0])

    def test_explicit_source_indices_survive_rejection_and_reordering(self):
        records = [
            row(BASE + 900_000, source_record_index=2),
            row(BASE + 1, source_record_index=1),  # rejected, but index is not renumbered
            row(BASE, source_record_index=0),
        ]
        result = normalise_records(records, context())
        self.assertEqual([c.source_record_index for c in result.candles], [0, 2])
        self.assertEqual(result.findings[0].source_record_index, 1)
        self.assertEqual(result.rejected_count, 1)

    def test_duplicate_explicit_source_indices_fail_closed(self):
        with self.assertRaises(ParserError):
            normalise_records([
                row(source_record_index=4),
                row(BASE + 900_000, source_record_index=4),
            ], context())

    def test_exact_duplicate_collapses_and_is_reported(self):
        result = normalise_records([row(), row()], context())
        self.assertEqual(len(result.candles), 1)
        self.assertEqual(result.duplicate_count, 1)

    def test_conflicting_duplicate_preserves_both_variants(self):
        result = normalise_records([row(), row(close=11.5, high=12.5)], context())
        self.assertEqual(len(result.candles), 2)
        self.assertEqual(result.conflict_count, 1)
        self.assertIn("CONFLICTING_CANDLE", [f.code for f in result.findings])

    def test_unconfirmed_candle_is_not_counted_as_closed(self):
        result = normalise_records([row(candle_status="unconfirmed")], context())
        report = assess_completeness(result, expected_count=1)
        self.assertEqual(report["unique_closed_count"], 0)
        self.assertFalse(report["coverage_achieved"])

    def test_unknown_candle_status_is_rejected(self):
        result = normalise_records([row(candle_status="maybe")], context())
        self.assertEqual(result.rejected_count, 1)

    def test_gap_is_reported(self):
        result = normalise_records([row(BASE), row(BASE + 2 * 900_000)], context())
        report = assess_completeness(result, expected_count=2)
        self.assertEqual(report["gap_count"], 1)
        self.assertFalse(report["coverage_achieved"])

    def test_forming_candle_cannot_bridge_gap_between_closed_candles(self):
        result = normalise_records([
            row(BASE),
            row(BASE + 900_000, candle_status="forming"),
            row(BASE + 2 * 900_000),
        ], context())
        report = assess_completeness(result, expected_count=2)
        self.assertEqual(report["unique_closed_count"], 2)
        self.assertEqual(report["gap_count"], 1)
        self.assertFalse(report["coverage_achieved"])

    def test_exactly_100_consecutive_closed_candles_complete(self):
        records = [row(BASE + i * 900_000) for i in range(100)]
        result = normalise_records(records, context())
        report = assess_completeness(result, expected_count=100)
        self.assertTrue(report["coverage_achieved"])
        self.assertEqual(report["unique_closed_count"], 100)

    def test_100_rows_with_duplicate_does_not_claim_100_unique(self):
        records = [row(BASE + i * 900_000) for i in range(99)] + [row(BASE + 98 * 900_000)]
        result = normalise_records(records, context())
        report = assess_completeness(result, expected_count=100)
        self.assertEqual(report["duplicate_count"], 1)
        self.assertFalse(report["coverage_achieved"])

    def test_replay_hashes_are_deterministic(self):
        records = [row(BASE), row(BASE + 900_000)]
        a = normalise_records(records, context())
        b = normalise_records(records, context())
        self.assertEqual(a.input_sha256, b.input_sha256)
        self.assertEqual(a.output_sha256, b.output_sha256)

    def test_input_hash_changes_when_input_changes(self):
        a = normalise_records([row()], context())
        b = normalise_records([row(close=10.5, high=12)], context())
        self.assertNotEqual(a.input_sha256, b.input_sha256)

    def test_unsupported_interval_fails_closed(self):
        ctx = context()
        ctx = ParseContext(**{**ctx.__dict__, "interval": "1h"})
        with self.assertRaises(ParserError):
            normalise_records([row()], ctx)


if __name__ == "__main__":
    unittest.main()
