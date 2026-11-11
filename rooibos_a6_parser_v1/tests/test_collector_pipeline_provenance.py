import unittest

from rooibos_a1.models import DiscoveryContext, normalize_observation as normalize_a1
from rooibos_a2.normalizer import MarketObservationInput
from rooibos_collectors.a1_pipeline import validate_a1_source_record_coverage
from rooibos_collectors.a2_pipeline import validate_a2_source_record_coverage

MINT = "So11111111111111111111111111111111111111112"


class CollectorSourceCoverageTests(unittest.TestCase):
    def _a1(self, index, count=2):
        context = DiscoveryContext(
            "solana", MINT, "fixture_source", "fixture_endpoint",
            "run-a1", "raw-a1", index, "2026-11-11T10:00:00Z",
            source_record_count=count,
        )
        return normalize_a1(context, {"symbol": f"T{index}"}, field_map={"symbol": "symbol"})

    def _a2(self, index, count=2):
        return MarketObservationInput(
            chain="solana", mint_address=MINT, observed_at="2026-11-11T10:00:00Z",
            source_id="fixture_source", endpoint_id="fixture_endpoint",
            run_id="run-a2", raw_response_id="raw-a2",
            source_record_index=index, metrics=(), source_record_count=count,
        )

    def test_a1_requires_exact_original_record_coverage(self):
        validate_a1_source_record_coverage([self._a1(0), self._a1(1)])

    def test_a1_rejects_omitted_record(self):
        with self.assertRaisesRegex(ValueError, "omitted or altered"):
            validate_a1_source_record_coverage([self._a1(0)])

    def test_a1_rejects_duplicate_source_index(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_a1_source_record_coverage([self._a1(0), self._a1(0)])

    def test_a1_requires_adapter_supplied_source_count(self):
        with self.assertRaisesRegex(ValueError, "must supply"):
            validate_a1_source_record_coverage([self._a1(0, count=None)])

    def test_a2_requires_exact_original_record_coverage_before_metric_fanout(self):
        validate_a2_source_record_coverage([self._a2(0), self._a2(1)])

    def test_a2_rejects_omitted_source_record(self):
        with self.assertRaisesRegex(ValueError, "omitted or altered"):
            validate_a2_source_record_coverage([self._a2(0)])

    def test_a2_rejects_duplicate_source_record(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_a2_source_record_coverage([self._a2(0), self._a2(0)])

    def test_a2_requires_adapter_supplied_source_count(self):
        with self.assertRaisesRegex(ValueError, "must supply"):
            validate_a2_source_record_coverage([self._a2(0, count=None)])
 
    def test_a1_rejects_boolean_source_index(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            validate_a1_source_record_coverage([self._a1(0), self._a1(True)])

    def test_a1_rejects_float_source_index(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            validate_a1_source_record_coverage([self._a1(0), self._a1(1.0)])

    def test_a2_rejects_boolean_source_index(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            validate_a2_source_record_coverage([self._a2(0), self._a2(True)])

    def test_a2_rejects_float_source_index(self):
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            validate_a2_source_record_coverage([self._a2(0), self._a2(1.0)])


if __name__ == "__main__":
    unittest.main()
