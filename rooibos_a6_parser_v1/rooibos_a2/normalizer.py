"""Provider-neutral A2 market/liquidity observation normalisation.

No provider field names are assumed. A source adapter must explicitly map
verified fields into MarketMetricInput records.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib, json, math
from typing import Any, Iterable

METRIC_UNITS = {
    "price": "quote_per_token",
    "market_cap": "currency",
    "fully_diluted_valuation": "currency",
    "liquidity": "currency",
    "pool_base_reserve": "token",
    "pool_quote_reserve": "quote_token",
    "volume": "currency",
    "buy_count": "count",
    "sell_count": "count",
    "trade_count": "count",
    "holder_count": "count",
    "market_name": "text",
    "market_type": "text",
    "quote_asset": "address",
    "pool_address": "address",
}

@dataclass(frozen=True)
class MarketMetricInput:
    metric_code: str
    value: float | int | str | None
    value_status: str = "present"  # present, missing, unavailable, not_reported, not_applicable
    unit: str | None = None
    quote_currency: str | None = None
    window_seconds: int | None = None
    measurement_kind: str = "observed"  # observed, estimated, unknown
    metadata: dict[str, Any] | None = None

@dataclass(frozen=True)
class MarketObservationInput:
    chain: str
    mint_address: str
    observed_at: str
    source_id: str
    endpoint_id: str
    run_id: str
    raw_response_id: str
    source_record_index: int
    metrics: tuple[MarketMetricInput, ...]
    market_id: str | None = None
    pool_address: str | None = None
    market_scope: str = "unknown"  # token_aggregated, pool_specific, unknown
    normalization_version: str = "a2-normalization-v1.0.0"
    source_record_metadata: dict[str, Any] | None = None

@dataclass(frozen=True)
class A2Finding:
    code: str
    severity: str
    metric_code: str | None
    detail: str

@dataclass(frozen=True)
class NormalizedMetric:
    observation_id: str
    chain: str
    mint_address: str
    market_id: str | None
    pool_address: str | None
    market_scope: str
    metric_code: str
    numeric_value: float | None
    text_value: str | None
    value_status: str
    unit: str
    quote_currency: str | None
    window_seconds: int | None
    measurement_kind: str
    observed_at: str
    source_id: str
    endpoint_id: str
    run_id: str
    raw_response_id: str
    source_record_index: int
    normalization_version: str
    metadata_json: str
    content_sha256: str

@dataclass
class A2Result:
    observations: list[NormalizedMetric]
    findings: list[A2Finding]
    received_metric_count: int
    accepted_metric_count: int
    rejected_metric_count: int

    def manifest(self) -> dict[str, Any]:
        body = {
            "normalization_version": "a2-normalization-v1.0.0",
            "received_metric_count": self.received_metric_count,
            "accepted_metric_count": self.accepted_metric_count,
            "rejected_metric_count": self.rejected_metric_count,
            "finding_count": len(self.findings),
            "observations": [asdict(o) for o in self.observations],
            "findings": [asdict(f) for f in self.findings],
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        body["manifest_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
        return body

def _valid_iso(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False

def normalize_observation(item: MarketObservationInput) -> A2Result:
    findings: list[A2Finding] = []
    output: list[NormalizedMetric] = []
    rejected = 0
    if not item.chain or not item.mint_address or not item.source_id or not item.endpoint_id or not item.run_id or not item.raw_response_id:
        raise ValueError("chain, mint, source, endpoint, run and raw response references are required")
    if not _valid_iso(item.observed_at):
        raise ValueError("observed_at must be an ISO-8601 timestamp")
    parsed_time = datetime.fromisoformat(item.observed_at.replace("Z", "+00:00"))
    if parsed_time.tzinfo is None:
        raise ValueError("observed_at must include an explicit timezone offset")
    if isinstance(item.source_record_index, bool) or not isinstance(item.source_record_index, int) or item.source_record_index < 0:
        raise ValueError("source_record_index must be a non-negative integer")
    if item.market_scope not in {"token_aggregated", "pool_specific", "unknown"}:
        raise ValueError("unsupported market_scope")
    if item.market_scope == "pool_specific" and not item.pool_address and not item.market_id:
        findings.append(A2Finding("POOL_IDENTITY_MISSING", "error", None, "Pool-specific observation lacks pool/market identity"))
    if not item.metrics:
        findings.append(A2Finding("NO_METRICS", "warning", None, "Source record contains no mapped metrics"))
    seen_metric_codes: set[str] = set()
    for metric in item.metrics:
        code = metric.metric_code
        if code in seen_metric_codes:
            findings.append(A2Finding("DUPLICATE_METRIC_MAPPING", "error", code, "A source record maps the same metric more than once")); rejected += 1; continue
        seen_metric_codes.add(code)
        if code not in METRIC_UNITS:
            findings.append(A2Finding("UNKNOWN_METRIC", "error", code, "Metric is not in the versioned A2 registry")); rejected += 1; continue
        if metric.value_status not in {"present", "missing", "unavailable", "not_reported", "not_applicable"}:
            findings.append(A2Finding("INVALID_VALUE_STATUS", "error", code, "Unsupported value status")); rejected += 1; continue
        if metric.measurement_kind not in {"observed", "estimated", "unknown"}:
            findings.append(A2Finding("INVALID_MEASUREMENT_KIND", "error", code, "Unsupported observed/estimated classification")); rejected += 1; continue
        expected_unit = METRIC_UNITS[code]
        unit = metric.unit or expected_unit
        if not isinstance(unit, str) or not unit.strip():
            findings.append(A2Finding("INVALID_UNIT", "error", code, "Metric unit must be a non-empty explicit label")); rejected += 1; continue
        if metric.value_status == "present":
            if metric.value is None:
                findings.append(A2Finding("PRESENT_VALUE_NULL", "error", code, "Present value cannot be NULL")); rejected += 1; continue
            if expected_unit in {"text", "address"}:
                if not isinstance(metric.value, str) or not metric.value.strip():
                    findings.append(A2Finding("INVALID_TEXT_VALUE", "error", code, "Expected non-empty text")); rejected += 1; continue
                numeric, text = None, metric.value.strip()
            else:
                if isinstance(metric.value, bool) or not isinstance(metric.value, (int, float)) or not math.isfinite(float(metric.value)):
                    findings.append(A2Finding("INVALID_NUMERIC_VALUE", "error", code, "Expected finite numeric value")); rejected += 1; continue
                if float(metric.value) < 0:
                    findings.append(A2Finding("NEGATIVE_VALUE", "error", code, "Negative market metric is not accepted")); rejected += 1; continue
                if code == "price" and float(metric.value) <= 0:
                    findings.append(A2Finding("NONPOSITIVE_PRICE", "error", code, "Present token price must be positive")); rejected += 1; continue
                numeric, text = float(metric.value), None
        else:
            if metric.value is not None:
                findings.append(A2Finding("NONPRESENT_VALUE_HAS_VALUE", "error", code, "Non-present value status must not carry a value")); rejected += 1; continue
            numeric, text = None, None
        if metric.window_seconds is not None and (isinstance(metric.window_seconds, bool) or metric.window_seconds <= 0):
            findings.append(A2Finding("INVALID_WINDOW", "error", code, "Measurement window must be positive seconds")); rejected += 1; continue
        if code == "volume" and metric.window_seconds is None:
            findings.append(A2Finding("VOLUME_WINDOW_UNKNOWN", "warning", code, "Volume retained but cannot be compared without a measurement window"))
        if code in {"price", "pool_quote_reserve"} and not metric.quote_currency:
            findings.append(A2Finding("QUOTE_CURRENCY_UNKNOWN", "warning", code, "Quote currency is not identified"))
        metadata = json.dumps(metric.metadata or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        identity = {
            "chain": item.chain, "mint": item.mint_address, "market": item.market_id,
            "pool": item.pool_address, "scope": item.market_scope, "metric": code,
            "observed_at": item.observed_at, "source": item.source_id, "endpoint": item.endpoint_id,
            "raw": item.raw_response_id, "record_index": item.source_record_index,
            "version": item.normalization_version, "value": numeric if numeric is not None else text,
            "status": metric.value_status, "unit": unit, "quote": metric.quote_currency,
            "window": metric.window_seconds, "kind": metric.measurement_kind,
            # Metadata is immutable observation content, not a replay-mutable annotation.
            "metadata": metric.metadata or {},
        }
        content = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(content.encode()).hexdigest()
        observation_id = digest[:32]
        output.append(NormalizedMetric(
            observation_id,item.chain,item.mint_address,item.market_id,item.pool_address,item.market_scope,
            code,numeric,text,metric.value_status,unit,metric.quote_currency,metric.window_seconds,
            metric.measurement_kind,item.observed_at,item.source_id,item.endpoint_id,item.run_id,
            item.raw_response_id,item.source_record_index,item.normalization_version,metadata,digest
        ))
    return A2Result(output, findings, len(item.metrics), len(output), rejected)
