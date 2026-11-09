# Rooibos A2 Market & Liquidity Integration v1.0.0

**Status:** Provider-neutral local implementation; synthetic integration tests passed. Not deployed or connected to Moby.

## Scope

A2 stores timestamped market observations independently of the OHLCV candle model. The metric registry currently covers:

- Token price
- Market capitalisation
- Fully diluted valuation
- Liquidity
- Pool base and quote reserves
- Volume (with explicit measurement window)
- Buy, sell and total trade counts
- Holder count
- Market name/type, quote asset and pool address metadata

This is a canonical ingestion interface, not a Moby response parser. A source-specific adapter must map verified provider fields into `MarketObservationInput` and `MarketMetricInput`. No provider field names, units, volume windows, or estimation semantics are guessed.

## Evidence and missingness

Every observation is linked to source, endpoint, collection run, immutable raw response, source record index and normalization version. Numeric zero is retained as a real value. Missing, unavailable, not reported and not applicable are separate states and are not coerced to zero. Observed, estimated and unknown measurement kinds remain distinct.

Pool-specific and token-aggregated observations remain separate. Pool-specific observations without a market/pool identity receive a quality finding. A non-null market ID is checked against the registered source, chain and mint at persistence time.

## Cross-source comparisons

Comparison only proceeds when all records have matching:
- Chain and mint
- Metric
- Market scope and market/pool identity
- Unit and quote currency
- Measurement window
- Measurement kind
- Present numeric values
- Distinct source IDs
- Observation times within the configured tolerance (default 60 seconds)

The comparison reports within-tolerance or disagreement. It does not average observations, choose a winner, or overwrite source records. Incompatible observations are labelled not-comparable.

## Storage

Migration 004 adds:
- `market_observations`
- `market_observation_comparisons`
- Supporting indexes and append-only update/delete triggers

All observations require a matching raw response/run/source/endpoint provenance chain. Persistence is idempotent. New evidence creates new observations; historical observations are not updated. A13 validation is not implemented here; records remain unvalidated and are not approved for research snapshots.

## Explicit exclusions

- No Moby-specific response decoder or live endpoint configuration.
- No Moby requests or credentials.
- No inferred USD conversions or unit conversions.
- No assumed volume windows or provider-estimated/observed classification.
- No liquidity ranking, candidate filtering, investment interpretation or recommendations.
- No A13 approval, Chart Engine integration, or paper portfolio changes.

## Verification

Full package suite: **61 tests passed**. Python compilation passed. Tests cover missing versus zero, metric validation, provenance, append-only storage, idempotent replay, market scope separation, cross-source disagreement, source independence and time-window comparability.
