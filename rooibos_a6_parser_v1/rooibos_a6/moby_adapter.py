"""Explicit-schema adapter for Moby's newest-first parallel OHLCV arrays.

Moby's MET response envelope and concrete field names are intentionally not
assumed here. A mapping must be supplied from a genuine, reviewed fixture.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class MobyOHLCVMappingError(ValueError):
    """The supplied Moby response mapping is incomplete or inconsistent."""


@dataclass(frozen=True)
class MobyParallelOHLCVMapping:
    """Reviewed response mapping; every provider-specific name is explicit."""

    envelope_path: tuple[str, ...]
    timestamp_field: str
    open_field: str
    high_field: str
    low_field: str
    close_field: str
    volume_field: str
    timestamp_unit: str
    volume_unit: str
    status_field: str | None = None
    status_values: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        required = (
            self.timestamp_field, self.open_field, self.high_field,
            self.low_field, self.close_field, self.volume_field,
        )
        if any(not isinstance(name, str) or not name for name in required):
            raise MobyOHLCVMappingError("Every parallel-array field name must be explicit")
        if self.timestamp_unit not in {"seconds", "milliseconds"}:
            raise MobyOHLCVMappingError("timestamp_unit must be explicitly seconds or milliseconds")
        if not self.volume_unit:
            raise MobyOHLCVMappingError("volume_unit must be explicitly defined")
        if self.status_field is None and self.status_values:
            raise MobyOHLCVMappingError("status_values requires an explicit status_field")
        if self.status_field is not None and not self.status_values:
            raise MobyOHLCVMappingError("A status field requires an explicit reviewed status mapping")
        if self.status_values and any(
            value not in {"closed", "forming", "unconfirmed"}
            for value in self.status_values.values()
        ):
            raise MobyOHLCVMappingError("Status mappings must resolve to canonical candle statuses")


def _at_path(payload: Any, path: tuple[str, ...]) -> Any:
    value = payload
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            raise MobyOHLCVMappingError(f"Configured response envelope path is missing at {key!r}")
        value = value[key]
    return value


def _timestamp_ms(value: Any, unit: str) -> int:
    if isinstance(value, bool) or value is None:
        raise MobyOHLCVMappingError("Timestamp array contains a non-numeric value")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise MobyOHLCVMappingError("Timestamp array contains a non-numeric value") from None
    if not number.is_finite() or number != number.to_integral_value():
        raise MobyOHLCVMappingError("Timestamps must be finite integral epoch values")
    result = int(number) * (1000 if unit == "seconds" else 1)
    if result <= 0:
        raise MobyOHLCVMappingError("Timestamps must be positive Unix epoch values")
    return result


def decode_moby_parallel_ohlcv(
    payload: Any, mapping: MobyParallelOHLCVMapping, *,
    include_in_progress_candle: bool | None = None,
) -> list[dict[str, Any]]:
    """Decode mapped parallel arrays without guessing names or semantic units.

    Moby's newest-first ordering is preserved so A6's source_record_index
    continues to identify the original response-array position. A6 later sorts
    canonical candles chronologically. Without a reviewed closure mapping,
    every candle is explicitly marked unconfirmed.
    """
    envelope = _at_path(payload, mapping.envelope_path)
    if not isinstance(envelope, Mapping):
        raise MobyOHLCVMappingError("Configured OHLCV envelope must be an object")

    fields = {
        "timestamp_ms": mapping.timestamp_field,
        "open": mapping.open_field,
        "high": mapping.high_field,
        "low": mapping.low_field,
        "close": mapping.close_field,
        "volume": mapping.volume_field,
    }
    arrays: dict[str, list[Any]] = {}
    for canonical, provider_field in fields.items():
        values = envelope.get(provider_field)
        if not isinstance(values, list):
            raise MobyOHLCVMappingError(
                f"Configured provider field {provider_field!r} must be a parallel array"
            )
        arrays[canonical] = values

    lengths = {len(values) for values in arrays.values()}
    if len(lengths) != 1:
        raise MobyOHLCVMappingError("OHLCV parallel arrays have unequal lengths")
    count = next(iter(lengths), 0)

    statuses: list[Any] | None = None
    if mapping.status_field is not None:
        statuses = envelope.get(mapping.status_field)
        if not isinstance(statuses, list) or len(statuses) != count:
            raise MobyOHLCVMappingError("Configured candle-status array is missing or has unequal length")

    output = []
    for index in range(count):
        row = {
            name: values[index] for name, values in arrays.items()
        }
        row["timestamp_ms"] = _timestamp_ms(row["timestamp_ms"], mapping.timestamp_unit)
        if statuses is not None and mapping.status_values is not None:
            status = mapping.status_values.get(str(statuses[index]), "unconfirmed")
        elif include_in_progress_candle is False:
            status = "closed"
        elif include_in_progress_candle is True:
            status = "forming" if index == 0 else "closed"
        else:
            status = "unconfirmed"
        row["candle_status"] = status
        # Coordinates refer to the immutable provider arrays, not decoded
        # output order. Repeating the count lets the pipeline detect omissions.
        row["source_record_index"] = index
        row["source_record_count"] = count
        output.append(row)
    return output
