"""Conservative comparisons: only like-for-like observations are compared."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from .normalizer import NormalizedMetric

@dataclass(frozen=True)
class ComparisonResult:
    outcome: str
    comparable_count: int
    minimum_value: float | None
    maximum_value: float | None
    spread_pct: float | None
    observation_ids: tuple[str, ...]
    reason: str

def compare_observations(observations: list[NormalizedMetric], *, tolerance_pct: float = 5.0, max_time_delta_seconds: int = 60) -> ComparisonResult:
    if tolerance_pct < 0:
        raise ValueError("tolerance_pct must be non-negative")
    if max_time_delta_seconds < 0:
        raise ValueError("max_time_delta_seconds must be non-negative")
    if len(observations) < 2:
        return ComparisonResult("insufficient",0,None,None,None,tuple(o.observation_id for o in observations),"At least two observations are required")
    first = observations[0]
    # Keep market scope and identity separate. Pool-specific values are only
    # comparable for the same pool; token-aggregated values compare only with
    # token-aggregated values. Metric window and denomination must match.
    times = [datetime.fromisoformat(o.observed_at.replace("Z", "+00:00")) for o in observations]
    time_spread = (max(times) - min(times)).total_seconds()
    unique_sources = {o.source_id for o in observations}
    comparable = len(unique_sources) == len(observations) and time_spread <= max_time_delta_seconds and all(
        o.chain == first.chain and o.mint_address == first.mint_address and
        o.metric_code == first.metric_code and o.market_scope == first.market_scope and
        o.market_id == first.market_id and o.pool_address == first.pool_address and
        o.unit == first.unit and o.quote_currency == first.quote_currency and
        o.window_seconds == first.window_seconds and o.measurement_kind == first.measurement_kind and o.value_status == "present" and
        o.numeric_value is not None and first.value_status == "present" and first.numeric_value is not None
        for o in observations
    )
    if not comparable:
        return ComparisonResult("not_comparable",0,None,None,None,tuple(o.observation_id for o in observations),"Requires distinct sources and matching asset, metric, scope, market, unit, quote currency, window, measurement kind and nearby observation times")
    values = [float(o.numeric_value) for o in observations]
    low, high = min(values), max(values)
    midpoint = (low + high) / 2
    spread = ((high-low)/midpoint*100) if midpoint else 0.0
    outcome = "within_tolerance" if spread <= tolerance_pct else "disagreement"
    return ComparisonResult(outcome,len(values),low,high,spread,tuple(o.observation_id for o in observations),"No source is selected as winner; all observations remain preserved")
