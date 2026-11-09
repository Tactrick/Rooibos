# Rooibos Chart Engine Runtime Integration Contract

**Target:** historical Chart Engine v0.3.5 runtime  
**Baseline:** collector pipeline v1.5.0  
**Status:** integration specification prepared; runtime wiring NOT implemented because the v0.3.5 source package is not present in the current workspace.

## 1. Objective

Make A13 approval a mandatory data-access boundary for every Chart Engine OHLCV input. Preserve the existing chart calculations, drawing algorithms, feature extraction, pattern detection, support/resistance logic and output schemas unless a separately approved change is required. This task changes the provenance and access path, not the analytical method.

## 2. Required runtime flow

```text
Chart Engine request
  -> ChartDataService (single approved entry point)
      -> resolve persisted A13 snapshot decision
      -> resolve exact immutable record membership for that snapshot
      -> ChartSnapshotReader.load(...)
          -> authorize persisted A13 decision and hashes
          -> validate dataset, policy and freshness
          -> validate exact chain/mint and candle structure
          -> return immutable ChartSnapshot
      -> existing Chart Engine analysis functions
```

The Chart Engine analysis layer receives a `ChartSnapshot` (or a deliberately narrow immutable candle sequence derived from it). It must not receive a raw collector database connection, raw collector table rows, an A6 storage object, or an alternate OHLCV loader.

## 3. Contract for the single entry point

The runtime-facing service should expose one method equivalent to:

```python
load_chart_snapshot(
    *, snapshot_id: str, chain: str, mint: str, interval: str,
    expected_candles: int = 100,
) -> ChartSnapshot
```

The service, not the caller or analysis code, resolves:

- the exact A13 evidence records associated with `snapshot_id`;
- the persisted snapshot content hash;
- the persisted assessment hash;
- the policy-version allowlist;
- the configured maximum age and warning policy.

The service then invokes `ChartSnapshotReader.load`. Do not accept caller-provided hashes as authority from an untrusted agent or UI. They may be passed internally only after being read from the trusted persisted decision.

## 4. Mandatory deny conditions

The service must fail closed when any of the following applies:

- no persisted A13 decision exists;
- decision is quarantined, rejected, incomplete, or otherwise not approved;
- record membership differs from the persisted decision;
- snapshot content hash or assessment hash differs;
- policy version is not explicitly allowed;
- snapshot is stale under the configured freshness policy;
- warning approval is present but consumer policy does not explicitly allow it;
- dataset is not `a6_ohlcv`;
- chain or exact mint differs (case-sensitive; never lowercase Solana mint addresses);
- interval is unsupported or differs from the evidence;
- any candle is forming, unconfirmed, unknown, malformed or non-finite;
- timestamps duplicate, misalign or contain gaps;
- the required unique closed-candle count is not met.

Denial must occur before any candle is passed to indicator, feature, drawing, strategy or pattern code. Do not silently drop invalid records and continue with a smaller dataset.

## 5. Removal of bypasses

During runtime integration, enumerate every OHLCV read path, including:

- SQL in the Chart Engine runtime;
- imports or calls into `rooibos_a6.storage` or parser internals;
- helper functions that load candles from SQLite or files;
- cached/in-memory datasets populated before A13 approval;
- test, replay, fallback and recovery entry points;
- alternate API/provider paths.

Replace each path with the single approved service. If a cache is retained, its key must bind snapshot ID, content hash, assessment hash, policy version, chain, exact mint, interval and freshness context. Cache hits must not bypass authorization; revalidate the grant or use a bounded, revocable grant mechanism.

No direct collector database access should be available to the Chart Engine analysis process. Prefer a narrow service boundary/process identity over relying solely on developer convention.

## 6. Compatibility requirements

- Preserve the existing Chart Engine v0.3.5 calculation and output behaviour for equivalent approved candle inputs.
- Preserve the existing 15m / 100 closed-candle baseline unless the current source specification says otherwise.
- Keep canonical timestamps in milliseconds at the data boundary; perform any seconds conversion only in the established Chart Engine adapter and test round-trip behaviour.
- Preserve volume units and provenance. Do not assume volume is base-token, quote-token or USD volume without verified source semantics.
- Preserve source response IDs, record indices, snapshot ID and hashes in analysis-run provenance.
- Do not use manually drawn chart zones as training labels, ground truth or performance benchmarks.

## 7. Runtime integration acceptance criteria

The runtime change is accepted only when all are true:

1. All existing Chart Engine tests pass unchanged or have justified, reviewed updates.
2. Golden-fixture analysis outputs are equivalent for the same approved candle inputs, except for added provenance metadata.
3. Every runtime OHLCV entry point routes through the approved service.
4. A source audit finds no direct raw collector access in the Chart Engine analysis package.
5. An unapproved, missing, stale, altered, wrong-mint, forming, duplicate, gapped or undersized snapshot is denied before analysis starts.
6. The analysis-run record links to the exact A13 snapshot and approval decision.
7. Restart, replay and cache tests cannot reuse a stale or revoked approval.
8. Tests prove there is no fallback to raw A6 data when the gate raises an error.
9. A controlled test with a deliberately bypassed call path fails the architecture audit or a runtime enforcement test.
10. The full suite passes from a fresh archive extraction.

## 8. Not in scope

This integration does not change chart-drawing methodology, strategy rules, trading decisions, paper positions, Moby permissions, provider endpoint mappings or live collection. It does not authorize automatic paper positions or live trading.

## 9. Evidence required before implementation

The actual Chart Engine v0.3.5 source package is required to identify its real entry points, database access, cache paths, tests, schemas and compatibility constraints. Until that source is available, no claim is made that runtime bypasses have been removed.


### Lifecycle requirement added in v1.8.0

Chart Engine must consume only snapshots that have both an approved A13 decision and a registered lifecycle event. The read gate enforces lifecycle registration by default and denies withdrawn/superseded snapshots. Once production retention policy assignments are approved, instantiate the reader with retention enforcement enabled.
