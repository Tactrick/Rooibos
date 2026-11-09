"""Independent, fail-closed A13 quality validation. No network, credentials or trading."""
from datetime import datetime
from typing import Mapping, Iterable
from .models import EvidenceRecord, SourcePolicy, Finding, RecordAssessment, A13ValidationError, canonical_hash

REQUIRED_TEXT = ("record_id", "dataset_type", "chain", "mint", "source_id", "endpoint_id", "run_id", "raw_response_id", "observed_at", "normalization_version", "content_hash")
SUPPORTED = {"a1_discovery", "a2_market", "a6_ohlcv"}

def _iso(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False

def _finding(code, severity, message):
    return Finding(code, severity, message)

def assess_record(record: EvidenceRecord, source_policies: Mapping[str, SourcePolicy], *, assessed_at: str) -> RecordAssessment:
    errors, warnings = [], []
    for name in REQUIRED_TEXT:
        value = getattr(record, name, None)
        if not isinstance(value, str) or not value.strip():
            errors.append(_finding("missing_" + name, "error", f"Required provenance/identity field {name} is missing."))
    if record.dataset_type not in SUPPORTED:
        errors.append(_finding("unsupported_dataset_type", "error", "Dataset type is not supported by this A13 validator."))
    if not _iso(record.observed_at):
        errors.append(_finding("invalid_observed_at", "error", "observed_at must be an ISO-8601 timestamp."))
    if record.source_record_index is not None and (isinstance(record.source_record_index, bool) or not isinstance(record.source_record_index, int) or record.source_record_index < 0):
        errors.append(_finding("invalid_source_record_index", "error", "source_record_index must be a non-negative integer when supplied."))
    policy = source_policies.get(record.source_id)
    if policy is None or not policy.active:
        errors.append(_finding("source_not_approved", "error", "Source is not present in the active source policy registry."))
    elif record.endpoint_id not in policy.approved_endpoints:
        errors.append(_finding("endpoint_not_approved", "error", "Endpoint is not approved for this source."))
    elif policy.endpoint_scopes is not None:
        scope = policy.endpoint_scopes.get(record.endpoint_id, {})
        if record.dataset_type not in scope.get("dataset_types", ()):
            errors.append(_finding("dataset_not_approved", "error", "Dataset type is outside the approved endpoint scope."))
        if record.chain not in scope.get("chains", ()):
            errors.append(_finding("chain_not_approved", "error", "Chain is outside the approved endpoint scope."))
    else:
        if policy.allowed_dataset_types and record.dataset_type not in policy.allowed_dataset_types:
            errors.append(_finding("dataset_not_approved", "error", "Dataset type is outside the approved source scope."))
        if policy.allowed_chains and record.chain not in policy.allowed_chains:
            errors.append(_finding("chain_not_approved", "error", "Chain is outside the approved source scope."))
    if isinstance(record.payload, Mapping):
        expected = canonical_hash(record.payload)
        if record.content_hash and expected != record.content_hash:
            errors.append(_finding("content_hash_mismatch", "error", "Normalized payload hash does not match the supplied content hash."))
    else:
        errors.append(_finding("payload_not_object", "error", "Normalized evidence payload must be an object."))

    p = record.payload if isinstance(record.payload, Mapping) else {}
    if record.dataset_type == "a1_discovery":
        if not p.get("mint") or p.get("mint") != record.mint:
            errors.append(_finding("a1_mint_mismatch", "error", "A1 payload mint must exactly match the record identity; case is significant."))
        if p.get("chain") and p.get("chain") != record.chain:
            errors.append(_finding("a1_chain_mismatch", "error", "A1 payload chain conflicts with record identity."))
        # Metadata is optional. Missing fields are not evidence of a defect.
        if "symbol" not in p and "name" not in p:
            warnings.append(_finding("a1_sparse_metadata", "warning", "Discovery has no symbol or name; token identity remains the chain and mint."))
    elif record.dataset_type == "a2_market":
        required = ("metric", "unit", "measurement_kind", "market_scope")
        for key in required:
            if key not in p or p[key] is None:
                errors.append(_finding("a2_missing_" + key, "error", f"A2 market observation requires {key}."))
        if p.get("measurement_kind") not in (None, "observed", "estimated", "unknown"):
            errors.append(_finding("a2_invalid_measurement_kind", "error", "A2 measurement_kind is not recognized."))
        value_status = p.get("value_status", "present")
        if "value" not in p:
            errors.append(_finding("a2_missing_value", "error", "A2 market observation must carry a value or explicit missingness."))
        if value_status not in ("present", "missing", "unavailable", "not_reported", "not_applicable"):
            errors.append(_finding("a2_invalid_value_status", "error", "A2 value_status is not recognized."))
        value = p.get("value")
        if value_status == "present":
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                errors.append(_finding("a2_invalid_value", "error", "Present A2 values must be numeric or text according to the metric registry."))
            if isinstance(value, str) and p.get("unit") not in ("text", "address"):
                errors.append(_finding("a2_text_value_unit_mismatch", "error", "Text A2 values require a text or address unit."))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and p.get("unit") in ("text", "address"):
                errors.append(_finding("a2_numeric_value_unit_mismatch", "error", "Text/address A2 units cannot carry numeric values."))
        elif value is not None:
            errors.append(_finding("a2_missingness_value_mismatch", "error", "Non-present A2 values must be NULL while preserving their explicit status."))
        if p.get("measurement_kind") == "unknown" and value is not None:
            warnings.append(_finding("a2_unknown_with_value", "warning", "Unknown measurement kind has a value; review semantics."))
        if p.get("market_scope") == "pool_specific" and not p.get("market_id"):
            errors.append(_finding("a2_pool_without_market", "error", "Pool-specific observations require a market/pool identity."))
    elif record.dataset_type == "a6_ohlcv":
        required = ("open_time_ms", "open", "high", "low", "close", "volume", "interval_ms", "candle_status")
        for key in required:
            if key not in p or p[key] is None:
                errors.append(_finding("a6_missing_" + key, "error", f"A6 candle requires {key}."))
        numeric = ("open", "high", "low", "close", "volume")
        if all(k in p and isinstance(p[k], (int, float)) and not isinstance(p[k], bool) for k in numeric):
            o, h, l, c, v = (float(p[k]) for k in numeric)
            if min(o, h, l, c, v) < 0 or h < max(o, c, l) or l > min(o, c, h):
                errors.append(_finding("a6_invalid_ohlcv_relationship", "error", "OHLC values violate non-negative/high-low consistency."))
        if p.get("candle_status") not in (None, "closed", "forming", "unknown"):
            errors.append(_finding("a6_invalid_candle_status", "error", "candle_status must be closed, forming or unknown."))
        if p.get("candle_status") == "forming":
            errors.append(_finding("a6_forming_candle", "error", "Forming candles are not eligible for approved research snapshots."))
        if p.get("volume_unit") is None:
            warnings.append(_finding("a6_volume_unit_unknown", "warning", "Volume unit is not declared; do not compare as if units match."))
        if p.get("candle_status") == "unknown":
            warnings.append(_finding("a6_candle_status_unknown", "warning", "Candle closure is unconfirmed; it must not be treated as a closed candle."))
        if isinstance(p.get("interval_ms"), int) and isinstance(p.get("open_time_ms"), int) and p["interval_ms"] > 0 and p["open_time_ms"] % p["interval_ms"] != 0:
            warnings.append(_finding("a6_timestamp_alignment", "warning", "Candle open time is not aligned to the declared interval."))

    status = "rejected" if errors else ("valid_with_warnings" if warnings else "valid")
    return RecordAssessment(record.record_id, status, tuple(errors + warnings), assessed_at)
