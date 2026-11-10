"""Provider-neutral OHLCV normalisation core for Rooibos A6.

This module deliberately does not decode a Moby response envelope and does not
make network requests. A source-specific adapter must map verified provider
records into the documented provider-neutral input contract.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Optional

PARSER_NAME = "rooibos_a6_generic"
PARSER_VERSION = "1.0.0"
NORMALISATION_VERSION = "1.0.0"
INTERVAL_MS = {"15m": 900_000}
REQUIRED_FIELDS = ("timestamp_ms", "open", "high", "low", "close", "volume")


class ParserError(ValueError):
    """Fatal input-contract or structural error."""


@dataclass(frozen=True)
class ParseContext:
    chain: str
    asset_id: str
    mint_address: str
    interval: str
    source_id: str
    endpoint_id: str
    collection_run_id: str
    raw_response_ref: str
    market_id: Optional[str] = None
    volume_unit: Optional[str] = None
    parser_version: str = PARSER_VERSION
    normalisation_version: str = NORMALISATION_VERSION


@dataclass(frozen=True)
class Candle:
    chain: str
    asset_id: str
    mint_address: str
    market_id: Optional[str]
    interval: str
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    volume_unit: Optional[str]
    candle_status: str
    source_id: str
    endpoint_id: str
    collection_run_id: str
    raw_response_ref: str
    source_record_index: int
    parser_version: str
    normalisation_version: str


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    message: str
    source_record_index: Optional[int] = None
    candle_identity: Optional[str] = None


@dataclass
class ParseResult:
    candles: list[Candle] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    received_count: int = 0
    decoded_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    conflict_count: int = 0
    input_sha256: str = ""
    output_sha256: str = ""
    status: str = "completed"

    def manifest(self) -> dict[str, Any]:
        return {
            "parser_name": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "normalisation_version": NORMALISATION_VERSION,
            "status": self.status,
            "received_count": self.received_count,
            "decoded_count": self.decoded_count,
            "output_count": len(self.candles),
            "rejected_count": self.rejected_count,
            "duplicate_count": self.duplicate_count,
            "conflict_count": self.conflict_count,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "findings": [asdict(f) for f in self.findings],
        }


def _finite_number(value: Any, field_name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field_name} is null or not numeric")
    try:
        number = float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise ValueError(f"{field_name} is not numeric") from None
    if not math.isfinite(number):
        raise ValueError(f"{field_name} is not finite")
    return number


def _json_safe(value: Any) -> Any:
    """Convert non-standard numeric values into explicit deterministic markers."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            marker = "nan"
        else:
            marker = "positive_infinity" if value > 0 else "negative_infinity"
        return {"__nonfinite_float__": marker}
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _identity(c: Candle) -> tuple[Any, ...]:
    return (c.chain, c.mint_address, c.market_id, c.interval, c.open_time_ms, c.source_id)


def _candle_values(c: Candle) -> tuple[Any, ...]:
    return (c.open, c.high, c.low, c.close, c.volume, c.candle_status, c.volume_unit)


def normalise_records(records: Iterable[Mapping[str, Any]], context: ParseContext) -> ParseResult:
    """Normalise provider-neutral records; unknown provider fields are ignored.

    Input record contract: timestamp_ms, open, high, low, close, volume;
    optional candle_status ('closed'|'forming'|'unconfirmed').
    """
    if context.interval not in INTERVAL_MS:
        raise ParserError(f"Unsupported interval: {context.interval}")
    for name in ("chain", "asset_id", "mint_address", "source_id", "endpoint_id", "collection_run_id", "raw_response_ref"):
        if not getattr(context, name):
            raise ParserError(f"Missing parse context: {name}")

    materialised = list(records)
    if any(not isinstance(row, Mapping) for row in materialised):
        raise ParserError("Every provider-neutral record must be a mapping")

    result = ParseResult(received_count=len(materialised), decoded_count=len(materialised))
    result.input_sha256 = hashlib.sha256(_canonical_json(materialised).encode()).hexdigest()
    candidates: list[Candle] = []

    seen_source_indices: set[int] = set()
    for position, row in enumerate(materialised):
        source_index = row.get("source_record_index", position)
        if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index < 0:
            raise ParserError("source_record_index must be a non-negative integer")
        if source_index in seen_source_indices:
            raise ParserError(f"Duplicate source_record_index: {source_index}")
        seen_source_indices.add(source_index)
        index = source_index
        missing = [name for name in REQUIRED_FIELDS if name not in row]
        if missing:
            raise ParserError(f"Record {index} missing required fields: {', '.join(missing)}")
        try:
            timestamp = row["timestamp_ms"]
            if isinstance(timestamp, bool) or not isinstance(timestamp, int):
                raise ValueError("timestamp_ms must be an integer Unix-millisecond value")
            if timestamp <= 0:
                raise ValueError("timestamp_ms must be positive")
            interval_ms = INTERVAL_MS[context.interval]
            if timestamp % interval_ms:
                raise ValueError("timestamp is not aligned to the requested interval")

            prices = {name: _finite_number(row[name], name) for name in ("open", "high", "low", "close")}
            volume = _finite_number(row["volume"], "volume")
            if any(value <= 0 for value in prices.values()):
                raise ValueError("OHLC prices must be positive")
            if prices["high"] < max(prices["open"], prices["close"]):
                raise ValueError("high is below open or close")
            if prices["low"] > min(prices["open"], prices["close"]):
                raise ValueError("low is above open or close")
            if prices["high"] < prices["low"]:
                raise ValueError("high is below low")
            if volume < 0:
                raise ValueError("volume is negative")
            status = row.get("candle_status", "unconfirmed")
            if status not in ("closed", "forming", "unconfirmed"):
                raise ValueError("candle_status must be closed, forming or unconfirmed")

            candidates.append(Candle(
                chain=context.chain, asset_id=context.asset_id, mint_address=context.mint_address,
                market_id=context.market_id, interval=context.interval, open_time_ms=timestamp,
                open=prices["open"], high=prices["high"], low=prices["low"], close=prices["close"],
                volume=volume, volume_unit=context.volume_unit, candle_status=status,
                source_id=context.source_id, endpoint_id=context.endpoint_id,
                collection_run_id=context.collection_run_id, raw_response_ref=context.raw_response_ref,
                source_record_index=index, parser_version=context.parser_version,
                normalisation_version=context.normalisation_version,
            ))
        except ValueError as exc:
            result.rejected_count += 1
            result.findings.append(Finding("INVALID_CANDLE", "error", str(exc), index))

    # Resolve exact duplicates and conflicts without silently selecting a winner.
    grouped: dict[tuple[Any, ...], list[Candle]] = {}
    for candle in candidates:
        grouped.setdefault(_identity(candle), []).append(candle)
    for identity, group in grouped.items():
        identity_text = _canonical_json(identity)
        if len(group) == 1:
            result.candles.append(group[0])
        elif len({_candle_values(c) for c in group}) == 1:
            result.candles.append(group[0])
            result.duplicate_count += len(group) - 1
            result.findings.append(Finding("EXACT_DUPLICATE", "warning", "Identical candle repeated; retained one canonical record", group[1].source_record_index, identity_text))
        else:
            result.conflict_count += 1
            result.candles.extend(group)
            result.findings.append(Finding("CONFLICTING_CANDLE", "error", "Same candle identity has differing values; all variants preserved", group[0].source_record_index, identity_text))

    result.candles.sort(key=lambda c: (c.open_time_ms, c.source_record_index))
    result.status = "completed" if result.rejected_count == 0 and result.conflict_count == 0 else "completed_with_findings"
    serialised = [asdict(c) for c in result.candles]
    result.output_sha256 = hashlib.sha256(_canonical_json(serialised).encode()).hexdigest()
    return result


def assess_completeness(result: ParseResult, expected_count: int = 100) -> dict[str, Any]:
    """Assess consecutive closed-candle coverage for the configured interval."""
    if expected_count < 1:
        raise ValueError("expected_count must be at least 1")
    candles = result.candles
    timestamps = sorted({c.open_time_ms for c in candles})
    interval_ms = INTERVAL_MS["15m"]
    gaps = []
    for left, right in zip(timestamps, timestamps[1:]):
        if right - left > interval_ms:
            gaps.append({"after_ms": left, "before_ms": right, "missing_intervals": (right - left) // interval_ms - 1})
    closed = [c for c in candles if c.candle_status == "closed"]
    unique_closed = len({c.open_time_ms for c in closed})
    consecutive = len(timestamps) >= 1 and not gaps
    coverage_ok = unique_closed >= expected_count and consecutive and result.conflict_count == 0 and result.rejected_count == 0
    return {
        "expected_count": expected_count,
        "received_count": result.received_count,
        "decoded_count": result.decoded_count,
        "normalised_count": len(candles),
        "valid_count": len(candles) - sum(1 for f in result.findings if f.code == "CONFLICTING_CANDLE") * 2,
        "rejected_count": result.rejected_count,
        "duplicate_count": result.duplicate_count,
        "conflict_count": result.conflict_count,
        "unique_closed_count": unique_closed,
        "gap_count": sum(g["missing_intervals"] for g in gaps),
        "gaps": gaps,
        "earliest_timestamp_ms": timestamps[0] if timestamps else None,
        "latest_timestamp_ms": timestamps[-1] if timestamps else None,
        "latest_confirmed_closed_timestamp_ms": max((c.open_time_ms for c in closed), default=None),
        "consecutive": consecutive,
        "coverage_achieved": coverage_ok,
        "status": "complete" if coverage_ok else "incomplete_or_degraded",
    }
