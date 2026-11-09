# Chart Engine + A13 Runtime Acceptance Matrix

This matrix is the required test plan for wiring the A13 reader into the historical Chart Engine runtime. The current v1.5.0 package tests only the reader boundary; they do not satisfy runtime integration acceptance.

| ID | Test | Expected result | Current status |
|---|---|---|---|
| CE-A13-01 | Persisted approved A6 snapshot | Chart data loads | Reader boundary tested; runtime pending |
| CE-A13-02 | Missing A13 decision | Deny before analysis | Reader boundary tested; runtime pending |
| CE-A13-03 | Quarantined/rejected decision | Deny before analysis | Core gate coverage; runtime pending |
| CE-A13-04 | Record membership altered | Deny | Reader boundary tested; runtime pending |
| CE-A13-05 | Payload/hash altered | Deny | Reader boundary tested; runtime pending |
| CE-A13-06 | Assessment hash altered | Deny | Core gate coverage; runtime pending |
| CE-A13-07 | Disallowed policy version | Deny | Core gate coverage; runtime pending |
| CE-A13-08 | Stale snapshot | Deny | Core gate supports freshness; runtime pending |
| CE-A13-09 | Warning approval without opt-in | Deny | Core gate default; runtime pending |
| CE-A13-10 | Wrong chain or exact mint case | Deny | Mint boundary tested; runtime pending |
| CE-A13-11 | Wrong interval / unsupported interval | Deny | Reader validation; runtime pending |
| CE-A13-12 | Forming or unconfirmed candle | Deny | Reader boundary tested; runtime pending |
| CE-A13-13 | NaN, infinity, bool, invalid OHLC, negative volume | Deny | Reader validation; runtime pending |
| CE-A13-14 | Duplicate timestamps | Deny | Reader validation; runtime pending |
| CE-A13-15 | Missing interval/gap/misalignment | Deny | Reader boundary tested; runtime pending |
| CE-A13-16 | Fewer/more than required candles | Deny when exact count required | Reader supports exact count; runtime pending |
| CE-A13-17 | Gate raises exception | No fallback to raw A6 | Runtime pending |
| CE-A13-18 | Cache hit after approval revocation/staleness | Deny or re-authorize | Runtime pending |
| CE-A13-19 | Restart/replay | Same snapshot/hash decision; no bypass | Runtime pending |
| CE-A13-20 | Analysis run provenance | Snapshot, decision, hashes and policy recorded | Runtime pending |
| CE-A13-21 | Existing golden fixture | Analysis output preserved | Runtime pending; source required |
| CE-A13-22 | Static source access audit | No direct raw collector access | Audit utility added; actual source pending |
| CE-A13-23 | Runtime entry-point inventory | Every OHLCV path gated | Runtime pending; source required |
| CE-A13-24 | Fresh archive full suite | All tests pass | Runtime pending |

## Required negative-path assertion

For every deny test, assert both:

1. the request raises a controlled access-denied/error result; and
2. the Chart Engine analysis function, indicator pipeline and drawing pipeline were not called.

A test that only checks the exception is insufficient: it must prove no candle data escaped to downstream analysis before denial.

| CE-LC1 | Lifecycle | Snapshot approved but not registered | Denied | v1.8.0 A13 lifecycle gate |
| CE-LC2 | Lifecycle | Snapshot superseded or withdrawn | Denied | v1.8.0 A13 lifecycle gate |
| CE-LC3 | Retention | Retention enforcement enabled but no policy assigned | Denied | v1.8.0 A13 retention gate |
| CE-LC4 | Retention | Retention period expired | Denied; evidence retained | v1.8.0 A13 retention gate |
