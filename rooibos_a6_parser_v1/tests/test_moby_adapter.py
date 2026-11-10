import unittest

from rooibos_a6.moby_adapter import (
    MobyOHLCVMappingError,
    MobyParallelOHLCVMapping,
    decode_moby_parallel_ohlcv,
)


def mapping(**overrides):
    values = dict(
        envelope_path=("result",),
        timestamp_field="time",
        open_field="o",
        high_field="h",
        low_field="l",
        close_field="c",
        volume_field="v",
        timestamp_unit="seconds",
        volume_unit="explicitly_unverified",
    )
    values.update(overrides)
    return MobyParallelOHLCVMapping(**values)


class MobyParallelAdapterTests(unittest.TestCase):
    def test_decodes_explicit_parallel_arrays_and_converts_seconds(self):
        payload = {"result": {
            "time": [1800000900, 1800000000],
            "o": [11, 10], "h": [13, 12], "l": [10, 9],
            "c": [12, 11], "v": [200, 100],
        }}
        rows = decode_moby_parallel_ohlcv(payload, mapping())
        self.assertEqual([r["timestamp_ms"] for r in rows], [1800000900000, 1800000000000])
        self.assertEqual([r["candle_status"] for r in rows], ["unconfirmed", "unconfirmed"])
        self.assertEqual(rows[0]["volume"], 200)

    def test_preserves_newest_first_order_for_source_record_indices(self):
        payload = {"result": {
            "time": [1800000900, 1800000000],
            "o": [11, 10], "h": [13, 12], "l": [10, 9],
            "c": [12, 11], "v": [200, 100],
        }}
        rows = decode_moby_parallel_ohlcv(payload, mapping())
        self.assertGreater(rows[0]["timestamp_ms"], rows[1]["timestamp_ms"])

    def test_explicit_status_mapping_is_used(self):
        payload = {"result": {
            "time": [1800000000], "o": [10], "h": [12], "l": [9],
            "c": [11], "v": [100], "closed_flag": [1],
        }}
        config = mapping(
            status_field="closed_flag",
            status_values={"1": "closed", "0": "forming"},
        )
        self.assertEqual(decode_moby_parallel_ohlcv(payload, config)[0]["candle_status"], "closed")

    def test_unknown_status_is_conservatively_unconfirmed(self):
        payload = {"result": {
            "time": [1800000000], "o": [10], "h": [12], "l": [9],
            "c": [11], "v": [100], "closed_flag": ["unknown"],
        }}
        config = mapping(status_field="closed_flag", status_values={"yes": "closed"})
        self.assertEqual(decode_moby_parallel_ohlcv(payload, config)[0]["candle_status"], "unconfirmed")

    def test_unequal_parallel_array_lengths_rejected(self):
        payload = {"result": {
            "time": [1800000000], "o": [10, 11], "h": [12], "l": [9],
            "c": [11], "v": [100],
        }}
        with self.assertRaises(MobyOHLCVMappingError):
            decode_moby_parallel_ohlcv(payload, mapping())

    def test_missing_configured_field_rejected(self):
        payload = {"result": {
            "time": [1800000000], "o": [10], "h": [12], "l": [9], "c": [11],
        }}
        with self.assertRaises(MobyOHLCVMappingError):
            decode_moby_parallel_ohlcv(payload, mapping())

    def test_missing_envelope_rejected(self):
        with self.assertRaises(MobyOHLCVMappingError):
            decode_moby_parallel_ohlcv({}, mapping())

    def test_timestamp_unit_must_be_explicit(self):
        with self.assertRaises(MobyOHLCVMappingError):
            mapping(timestamp_unit="guess")

    def test_status_values_cannot_be_declared_without_status_field(self):
        with self.assertRaises(MobyOHLCVMappingError):
            mapping(status_values={"1": "closed"})

    def test_status_field_requires_explicit_semantic_mapping(self):
        with self.assertRaises(MobyOHLCVMappingError):
            mapping(status_field="closed_flag")

    def test_millisecond_timestamps_are_not_scaled(self):
        payload = {"result": {
            "time": [1800000000000], "o": [10], "h": [12], "l": [9],
            "c": [11], "v": [100],
        }}
        rows = decode_moby_parallel_ohlcv(payload, mapping(timestamp_unit="milliseconds"))
        self.assertEqual(rows[0]["timestamp_ms"], 1800000000000)


if __name__ == "__main__":
    unittest.main()
