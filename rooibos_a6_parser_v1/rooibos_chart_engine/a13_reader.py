"""Fail-closed A13-gated adapter for Chart Engine OHLCV consumption.

This is the controlled read boundary. It deliberately accepts already-resolved
A13 evidence records and never queries raw collector tables itself. The caller
must resolve the exact snapshot membership from the approved research store,
then pass those records here. The gate runs before any candle is exposed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import sqlite3
from typing import Iterable

from rooibos_a13.models import EvidenceRecord, canonical_hash
from rooibos_a13.read_gate import GateGrant, SnapshotAccessDenied, authorize_snapshot


@dataclass(frozen=True)
class ChartCandle:
    chain: str
    mint: str
    market_id: str | None
    interval: str
    interval_ms: int
    open_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    volume_unit: str | None
    source_id: str
    raw_response_id: str
    source_record_index: int | None


@dataclass(frozen=True)
class ChartSnapshot:
    grant: GateGrant
    chain: str
    mint: str
    interval: str
    candles: tuple[ChartCandle, ...]


_INTERVALS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
              "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _finite_number(payload: dict, field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SnapshotAccessDenied(f"A13-approved OHLCV payload has invalid {field}")
    return float(value)


class ChartSnapshotReader:
    """Only exposes closed, structurally valid OHLCV after persisted A13 approval."""

    def __init__(self, conn: sqlite3.Connection, *, allowed_policy_versions: set[str],
                 allow_warnings: bool = False, enforce_retention: bool = False):
        self._conn = conn
        self._allowed_policy_versions = set(allowed_policy_versions)
        self._allow_warnings = allow_warnings
        self._enforce_retention = enforce_retention

    def load(self, *, snapshot_id: str, records: Iterable[EvidenceRecord],
             expected_snapshot_hash: str, expected_assessments_hash: str,
             chain: str, mint: str, interval: str,
             now: str | None = None, max_age_seconds: int | None = None,
             expected_candles: int | None = None) -> ChartSnapshot:
        rows = list(records)
        # Authorization precedes parsing or returning any consumer-facing data.
        grant = authorize_snapshot(
            self._conn, snapshot_id=snapshot_id, records=rows,
            expected_snapshot_hash=expected_snapshot_hash,
            expected_assessments_hash=expected_assessments_hash,
            allowed_policy_versions=self._allowed_policy_versions,
            allow_warnings=self._allow_warnings, now=now,
            max_age_seconds=max_age_seconds, require_lifecycle=True,
            enforce_retention=self._enforce_retention,
        )
        if grant.dataset_type != "a6_ohlcv":
            raise SnapshotAccessDenied("Chart Engine accepts only A6 OHLCV snapshots")
        if interval not in _INTERVALS:
            raise SnapshotAccessDenied("unsupported Chart Engine interval")
        if not chain or not mint:
            raise SnapshotAccessDenied("chain and exact mint identity are required")

        candles: list[ChartCandle] = []
        for record in rows:
            if record.chain != chain or record.mint != mint:
                raise SnapshotAccessDenied("snapshot contains a different chain or exact mint")
            p = dict(record.payload)
            if canonical_hash(p) != record.content_hash:
                raise SnapshotAccessDenied("OHLCV payload hash does not match its A13 evidence record")
            if p.get("interval") != interval or p.get("interval_ms") != _INTERVALS[interval]:
                raise SnapshotAccessDenied("candle interval does not match requested interval")
            if p.get("candle_status") != "closed":
                raise SnapshotAccessDenied("Chart Engine refuses forming, unconfirmed or unknown candles")
            open_time = p.get("open_time_ms")
            if isinstance(open_time, bool) or not isinstance(open_time, int) or open_time < 0:
                raise SnapshotAccessDenied("invalid candle open timestamp")
            o, h, l, c, v = (_finite_number(p, f) for f in ("open", "high", "low", "close", "volume"))
            if min(o, h, l, c) <= 0 or v < 0 or h < max(o, c, l) or l > min(o, c, h):
                raise SnapshotAccessDenied("invalid OHLC price relationships or volume")
            candles.append(ChartCandle(
                chain=record.chain, mint=record.mint, market_id=p.get("market_id"),
                interval=interval, interval_ms=_INTERVALS[interval], open_time_ms=open_time,
                open=o, high=h, low=l, close=c, volume=v,
                volume_unit=p.get("volume_unit"), source_id=record.source_id,
                raw_response_id=record.raw_response_id,
                source_record_index=record.source_record_index,
            ))
        candles.sort(key=lambda c: c.open_time_ms)
        times = [c.open_time_ms for c in candles]
        if len(times) != len(set(times)):
            raise SnapshotAccessDenied("duplicate candle timestamps")
        if any(b - a != _INTERVALS[interval] for a, b in zip(times, times[1:])):
            raise SnapshotAccessDenied("OHLCV snapshot contains a gap or misaligned interval")
        if expected_candles is not None and len(candles) != expected_candles:
            raise SnapshotAccessDenied("candle count differs from Chart Engine requirement")
        return ChartSnapshot(grant, chain, mint, interval, tuple(candles))
