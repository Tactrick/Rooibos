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

## Moby get_token_ohlcv mapping status

A source-specific profile now maps the compact `t/o/h/l/c/v` column names and
millisecond timestamps. Public Moby tool descriptions corroborate that `t` is
Unix milliseconds, prices are USD, the series is newest-first, and the
in-progress candle is excluded by default (or appears at index zero when
requested). The complete output schema is not published in the accessible
first-party reference; compact OHLCV column names beyond `t`/`c` are therefore
treated as a provisional mapping pending a genuine captured response.

The decoder receives the captured `include_in_progress_candle` request value.
It marks all rows unconfirmed if that request evidence is absent; if true, only
row zero is forming; if false, returned rows are closed per the documented
tool contract. The mapping requires an explicitly verified volume unit; USD
price denomination alone is not treated as proof of volume denomination.

**Still blocked:** no genuine Moby MET response fixture, verified response
envelope, complete field schema, volume denomination or independent market
identity mapping is available. No production mapping is registered or enabled,
and no live Moby request has been made. The current profile is adapter code,
not production source approval.
## A6 request/response market-identity binding

The A6 raw-response pipeline now requires two independent checks before decoding:

- `request_identity_fields` explicitly maps the ParseContext mint and interval to the captured request parameters.
- `response_identity_fields` explicitly maps the mint identity returned inside the immutable response payload to a nested field path. Missing or mismatched response identity is rejected before decoding, candle persistence or A13 publication.

The response identity path is caller-supplied; no provider aliases or envelope paths are guessed. Synthetic tests cover matching identity, wrong-mint response and missing identity. The currently documented Moby OHLCV response description does not establish a verified returned mint-identity field or complete envelope. Therefore this new guard is proven only with synthetic fixtures; a genuine MET response fixture and explicit response identity mapping remain prerequisites for production integration. The Moby compact-array adapter must not be treated as independently proving token identity merely because the request specified a mint.

### A6 original source-record coordinates

The Moby parallel-array adapter now emits both `source_record_index` (the zero-based position in the original captured provider arrays) and `source_record_count` (the original array length) on every decoded row. It does not sort, filter, or collapse rows. The generic A6 normalizer honours an explicit source index when supplied, so rejected records retain their original index in findings and valid candles retain their original index after chronological sorting or duplicate handling. Duplicate explicit indices are structural errors.

The A6 raw-response pipeline requires decoded rows to carry a complete one-to-one index set for the declared original source count. Missing, repeated, altered, or filtered source records therefore fail closed before persistence/publication rather than silently shifting provenance. This requirement applies to pipeline decoders; direct provider-neutral parser use may still omit explicit indices and receive positional indices for synthetic/local workflows. Tests use synthetic payloads only. A genuine Moby response fixture remains outstanding.


## A13 exact source-coordinate publication links (migration 010)

A13 now stores an append-only per-record evidence-link row containing
`snapshot_id`, `record_id`, `raw_response_id`, and the original
`source_record_index`. The existing snapshot-to-raw-response links remain
for lifecycle and retention operations; they are not treated as sufficient
proof of record-level provenance.

Publication writes lifecycle registration, snapshot raw-response links, and
per-record source-coordinate links within one transaction boundary. The
read gate compares the complete persisted per-record relationship set against
the approved evidence records and denies access on any missing, extra, or
altered relationship. The source index is also bound into the approved
snapshot content hash. Migration 010 is additive; earlier migrations remain
immutable. Tests use synthetic local records only.

### A1/A2 original source-record coverage

The A1 and A2 raw-response pipelines now require their explicit provider
adapters to declare the original response-array length as
`source_record_count` on every decoded source record. Before persistence,
each pipeline verifies that all declared counts agree and that the decoded
records cover every original zero-based index exactly once. A2 validates
source-record coverage before expanding each source record into multiple
metric observations. Missing records, duplicate indices, inconsistent
counts, or omitted counts fail closed. Direct normalizer use may omit the
count for isolated local workflows; collector publication may not.

### A13 replacement lifecycle

The publication-safe replacement API is
`supersede_snapshot_for_records()`. It validates replacement decision
membership and content hash, then atomically supersedes the old snapshot and
registers the replacement with exact per-record raw-response/source-index
links. The legacy `supersede_snapshot()` API, which accepts only a list of raw
response IDs, now fails closed because it cannot prove record-level
provenance. Read authorization continues to require the exact per-record
links, regardless of whether lifecycle checks are otherwise requested.

