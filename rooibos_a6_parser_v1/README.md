# Rooibos Collector + A1/A2/A6 + A13 integration v1.8.0

**Status:** Local integration increment; migration chain and projection tests are synthetic. Not deployed, not connected to Moby, and not approved for live collection.

## Included

- Original collector framework, A6 provider-neutral OHLCV parser/storage, and A2 metric normalizer/comparison/storage from v1.3.0.
- A1 Token Discovery models, policy and append-only storage.
- A13 validation, governance, append-only assessment/decision storage and fail-closed snapshot read gate.
- Provider-neutral projection adapters: `rooibos_integrations/a13_projection.py`.
- Shared migration runner now applies migrations 001–008. Existing migration SQL 001–004 is unchanged; `moby_market.db` remains the database filename.

## Projection contracts

- A1: one A13 discovery evidence record per immutable discovery observation. Chain and mint are copied exactly; case is never normalized.
- A2: one A13 evidence record per normalized metric. Numeric and text/address values retain units; missing/unavailable/not-reported/not-applicable remain explicit NULL values plus `value_status`. Pool identity and market scope are preserved.
- A6: one A13 evidence record per canonical candle. OHLCV, interval, market identity, raw-response reference and source record index are retained. The caller must supply `observed_at` from collection/raw-response provenance; candle open time is never substituted for observation time. Parser `unconfirmed` maps to A13 `unknown`, never to `closed`; `forming` remains ineligible.
- All projections calculate a deterministic A13 payload hash and preserve source, endpoint, run, raw response and normalization version.

## Snapshot approval and consumption

`assess_and_store_snapshot()` runs A13 record validation, persists append-only assessments, creates a deterministic governance decision, and persists that decision. `authorize_snapshot()` must be called by every downstream research consumer with the exact records it intends to expose, expected snapshot hash, persisted assessment hash, allowed policy versions and freshness policy. It now requires an append-only lifecycle registration by default and denies withdrawn or superseded snapshots. Retention enforcement can additionally be enabled after an approved retention policy is assigned. Warning-approved snapshots are denied by default unless the consumer explicitly opts in. Raw collector tables must not be exposed through an ungated path.

## Migrations

- 001–004: original v1.3.0 migration SQL retained unchanged.
- 005–007: A1/A13 additive migrations from the v1.7.0 baseline retained unchanged.
- 008: append-only A13 lifecycle, retention policy, assignment and raw-response linkage.
- 005: append-only A1 discovery observations.
- 006: append-only A13 assessments and snapshot decisions.
- Runner checksum-checks all applied migrations and refuses checksum drift.

## Verification

Run from this directory:

```bash
python -m unittest discover -s tests -v
python -m compileall -q rooibos_collectors rooibos_a1 rooibos_a2 rooibos_a6 rooibos_a13 rooibos_db rooibos_integrations
```

No provider-specific Moby adapters, production endpoint approvals, network client, credentials, Chart Engine runtime wiring, agent runtime wiring, or trading capabilities are included. A6 MET fixture mapping remains blocked pending genuine fixture evidence. The A13 source registry is empty and proposed-not-approved. This is a tested integration seam, not end-to-end production acceptance.

## A13 snapshot lifecycle

Migration 008 adds append-only lifecycle events, hash-chained publication/supersession/withdrawal history, legal holds, immutable retention-policy versions and snapshot-to-raw-response links. Snapshot publication requires at least one raw-response link. Expired evidence can be denied for current research use, but is not deleted. No production retention durations are configured; see `docs/A13_SNAPSHOT_LIFECYCLE_GOVERNANCE.md`. Pre-008 snapshots require explicit lifecycle registration before they can pass the default read gate.

## A13 source registry

`config/a13_source_registry.json` is intentionally empty and deny-all. `rooibos_a13.source_registry` validates versioned source/endpoint scopes, expiry, dataset types and chains, and can produce the A13 validator policy mapping. It does not configure network endpoints or approve collection. See `docs/A13_SOURCE_REGISTRY_GOVERNANCE.md`.

## Original v1.3.0 documentation

The A2-specific metric registry and comparison details remain documented in `README_A2.md`. The original A6 parser and collector framework design notes are retained in `README_A6_BASELINE.md` for historical reference; this v1.4.0 README governs the integrated package status.
