"""Documented Moby get_token_ohlcv adapter profile."""
from __future__ import annotations

from typing import Any

from .moby_adapter import (
    MobyParallelOHLCVMapping, decode_moby_parallel_ohlcv,
)


def documented_moby_ohlcv_mapping(*, volume_unit: str, envelope_path: tuple[str, ...] = ()) -> MobyParallelOHLCVMapping:
    """Map the documented compact OHLCV keys; caller must verify volume units.

    Moby tool documentation identifies t as Unix milliseconds, prices as USD,
    and returns a newest-first series. The caller supplies volume denomination
    because that semantic must be independently confirmed before research use.
    """
    if not volume_unit or volume_unit in {"unknown", "unverified"}:
        raise ValueError("A verified volume_unit is required for this mapping")
    return MobyParallelOHLCVMapping(
        envelope_path=envelope_path, timestamp_field="t",
        open_field="o", high_field="h", low_field="l",
        close_field="c", volume_field="v",
        timestamp_unit="milliseconds", volume_unit=volume_unit,
    )


def decode_moby_get_token_ohlcv(
    payload: Any, mapping: MobyParallelOHLCVMapping, *,
    include_in_progress_candle: bool | None,
) -> list[dict[str, Any]]:
    """Decode a captured response using the captured request setting.

    Pass False only when the persisted request explicitly set
    include_in_progress_candle=false. Pass True when it was true. Pass None
    when request evidence is absent; in that case closure is not asserted.
    The Moby contract documents newest-first order and says the in-progress
    candle, when requested, is at index zero.
    """
    return decode_moby_parallel_ohlcv(
        payload, mapping, include_in_progress_candle=include_in_progress_candle,
    )
