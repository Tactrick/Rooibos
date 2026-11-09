import json
import tempfile
import unittest
from pathlib import Path

from rooibos_a13.source_registry import load_registry_data, SourceRegistryError

BASE = {
    "schema_version": "1.0.0", "registry_version": "0.1.0",
    "policy_version": "a13-source-policy/0.1.0", "status": "proposed_not_approved", "sources": []
}


def approved_data():
    d = json.loads(json.dumps(BASE))
    d["status"] = "approved"
    d["sources"] = [{
        "source_id": "fixture_provider", "display_name": "Fixture Provider",
        "independent_group": "fixture_provider_group", "approval_reference": "CHANGE-123",
        "approved_by": "reviewer-1", "approved_at": "2026-11-01T00:00:00Z",
        "expires_at": "2026-12-01T00:00:00Z", "endpoints": [{
            "endpoint_id": "fixture_ohlcv", "dataset_types": ["a6_ohlcv"],
            "chains": ["solana"], "evidence_fields": ["mint", "open_time_ms", "ohlcv", "raw_response_id"]
        }]
    }]
    return d


class SourceRegistryTests(unittest.TestCase):
    def test_default_registry_is_empty_and_deny_all(self):
        registry = load_registry_data(BASE)
        self.assertEqual(registry.source_policies(now="2026-11-09T12:00:00Z"), {})

    def test_proposed_registry_never_activates_sources(self):
        d = approved_data(); d["status"] = "proposed_not_approved"
        registry = load_registry_data(d)
        self.assertEqual(registry.source_policies(now="2026-11-09T12:00:00Z"), {})

    def test_approved_unexpired_source_yields_only_declared_endpoint(self):
        registry = load_registry_data(approved_data())
        policies = registry.source_policies(now="2026-11-09T12:00:00Z")
        self.assertEqual(tuple(policies["fixture_provider"].approved_endpoints), ("fixture_ohlcv",))
        self.assertEqual(policies["fixture_provider"].independent_group, "fixture_provider_group")

    def test_expired_source_is_excluded(self):
        d = approved_data(); d["sources"][0]["expires_at"] = "2026-11-08T00:00:00Z"
        registry = load_registry_data(d)
        self.assertEqual(registry.source_policies(now="2026-11-09T12:00:00Z"), {})

    def test_duplicate_source_rejected(self):
        d = approved_data(); d["sources"].append(dict(d["sources"][0]))
        with self.assertRaises(SourceRegistryError): load_registry_data(d)

    def test_unknown_dataset_rejected(self):
        d = approved_data(); d["sources"][0]["endpoints"][0]["dataset_types"] = ["wallet_trades"]
        with self.assertRaises(SourceRegistryError): load_registry_data(d)

    def test_timezone_required(self):
        d = approved_data(); d["sources"][0]["approved_at"] = "2026-11-01T00:00:00"
        with self.assertRaises(SourceRegistryError): load_registry_data(d)

    def test_expiry_must_follow_approval(self):
        d = approved_data(); d["sources"][0]["expires_at"] = "2026-10-01T00:00:00Z"
        with self.assertRaises(SourceRegistryError): load_registry_data(d)

    def test_registry_version_must_be_supported(self):
        d = json.loads(json.dumps(BASE)); d["schema_version"] = "99"
        with self.assertRaises(SourceRegistryError): load_registry_data(d)


if __name__ == "__main__": unittest.main()

class SourceRegistryScopeTests(unittest.TestCase):
    def test_endpoint_dataset_and_chain_scope_is_preserved(self):
        from rooibos_a13.models import EvidenceRecord
        from rooibos_a13.validator import assess_record
        registry = load_registry_data(approved_data())
        policies = registry.source_policies(now="2026-11-09T12:00:00Z")
        payload = {"open_time_ms": 0, "open": 1, "high": 1, "low": 1, "close": 1,
                   "volume": 1, "interval_ms": 900000, "candle_status": "closed", "volume_unit": "base_token"}
        from rooibos_a13.models import canonical_hash
        record = EvidenceRecord("r1", "a6_ohlcv", "ethereum", "Mint", "fixture_provider",
            "fixture_ohlcv", "run1", "raw1", "2026-11-09T12:00:00Z", "norm1",
            canonical_hash(payload), payload)
        assessment = assess_record(record, policies, assessed_at="2026-11-09T12:00:01Z")
        self.assertEqual(assessment.status, "rejected")
        codes = {f.code for f in assessment.findings}
        self.assertIn("chain_not_approved", codes)

