# Rooibos Common Collector Framework + A6 OHLCV + A2 Market/Liquidity v1.3.0

**Status:** Local implementation and synthetic acceptance tests passed. Not deployed and not connected to Moby.

## Included components

- `rooibos_collectors/framework.py`: fail-closed collector run manager, explicit endpoint-policy checks, request tickets, lifecycle transitions and raw-response capture orchestration. It records and authorises work but does not perform network requests.
- `rooibos_a6/parser.py`: provider-neutral OHLCV normalisation, quality findings, completeness assessment and deterministic hashes.
- `rooibos_a6/storage.py`: immutable raw-response persistence, parse-event manifests, idempotent canonical candle staging and provenance checks.
- `rooibos_a2/`: provider-neutral market/liquidity metric normalisation, explicit missingness, source-preserving comparisons and append-only persistence.
- `rooibos_db/database.py`: shared SQLite database foundation and migrations 001–003.
- `tests/`: database migration, collector framework, A6 parser/storage and A2 normalisation/comparison/storage integration tests.
- `examples/moby_market_example.db`: empty example database migrated to schema version 3.

## Collector framework behaviour

- Default deny: no endpoint policy means no collection permission.
- A policy must be explicitly marked validated and enabled before a run can be created.
- The database endpoint's source, name, method and path must exactly match the policy.
- Only GET endpoints are supported by this framework slice; trading, swap, buy, sell, order, wallet, portfolio and position endpoint names are denied.
- Request parameters must be explicitly allowlisted; numeric `limit` is bounded.
- Request tickets are persisted before any external request could occur.
- Raw responses are stored before parsing and linked to their request/run/source/endpoint.
- Collection lifecycle transitions are checked; terminal runs cannot be restarted.
- This module does not include an HTTP client, Moby key, credential reader, redirect handler or network access.

The actual Moby endpoint policies remain unvalidated candidates. In particular, `get_token_ohlcv` is not enabled for live collection until the genuine MET response fixture, exact request parameters, response mapping and provenance semantics are verified. The synthetic `validated=True` policy in tests is solely a local test fixture, not a production Moby approval.

## A6 database integration behaviour

1. Raw response must be persisted before parser output can be stored.
2. Raw response IDs cannot be reused with changed payload or evidence metadata.
3. Raw response rows remain protected by no-update/no-delete triggers.
4. Parser output must match the raw response's run, source and endpoint.
5. Asset identity must match the registered chain and mint; non-null market IDs must belong to that asset.
6. Canonical candles are inserted with `quality_status='unvalidated'`. This package does not approve data for research.
7. Replaying identical candles is idempotent; an existing candle with changed values is preserved and reported as a storage conflict.
8. Parser-conflicting variants are retained in the normalization-event manifest and are not forced into the unique canonical candle table.
9. Every parse attempt creates a normalization event, including replays.
10. Canonical persistence requires the run to have reached the `parsed` state (or be an idempotent replay from `normalised`).

## Schema migrations

- Migration 001: original shared collector schema.
- Migration 002: partial unique index for OHLCV candle identity when `market_id IS NULL`.
- Migration 003: `candle_status`, `volume_unit`, `source_record_index`, and `endpoint_id` columns on `ohlcv_candles`.
- Migration 004: append-only A2 `market_observations` and `market_observation_comparisons` tables, indexes and immutability triggers.

Migration 001 and 002 SQL are unchanged. The database filename remains `moby_market.db`.

## Verification

```bash
python -m unittest discover -s tests -v
python -m compileall -q rooibos_collectors rooibos_a6 rooibos_db
```

Latest local test run: **61 tests passed**.

## Exclusions and remaining gates

- No Moby network calls, adapter, or credential access.
- No guessed MET response envelope or field mapping.
- No A13 independent validation or research snapshot approval.
- No Chart Engine integration or live collection.
- No paper positions, trading actions or wallet operations.

A2 provider-neutral implementation is now included. Next pipeline work: A1 Token Discovery integration, followed by A13 independent validation and acceptance testing. A2 source-specific live acceptance and A6 live acceptance remain blocked on genuine provider fixtures and restricted adapter validation.


## MET parallel-array adapter scaffold

`rooibos_a6/moby_adapter.py` now provides an explicit-schema adapter for
Moby's confirmed newest-first parallel-array response shape. The adapter
requires the caller to supply the exact envelope path, array field names,
timestamp unit and volume unit. It preserves provider array order so A6's
`source_record_index` continues to refer to the original response position;
the A6 normalizer independently orders canonical candles chronologically.

Candle closure semantics are not inferred. Without an explicitly reviewed
status field and value mapping, every candle is marked `unconfirmed`. Unknown
status values also remain `unconfirmed`. Unequal array lengths, missing
configured fields, missing envelopes and ambiguous timestamp units are rejected.

This is adapter infrastructure only. No genuine MET response fixture has yet
verified the envelope, concrete array names, timestamp unit, volume semantics,
market identity or closure mapping. Therefore no MET mapping is instantiated,
no Moby endpoint is enabled, and the initial target of 100 closed 15-minute
candles cannot yet be claimed as achieved.
