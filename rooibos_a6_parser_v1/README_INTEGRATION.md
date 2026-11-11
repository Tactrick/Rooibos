# Rooibos Collector + A1/A2/A6 + A13 integration v1.5.1

**Status:** Local integration increment; migration chain and projection tests are synthetic. Not deployed, not connected to Moby, and not approved for live collection.

## Included

- Original collector framework, A6 provider-neutral OHLCV parser/storage, and A2 metric normalizer/comparison/storage from v1.3.0.
- A1 Token Discovery models, policy and append-only storage.
- A13 validation, governance, append-only assessment/decision storage and fail-closed snapshot read gate.
- Provider-neutral projection adapters: `rooibos_integrations/a13_projection.py`.
- Shared migration runner now applies migrations 001–007. Existing migration SQL 001–004 is unchanged; `moby_market.db` remains the database filename.

## Projection contracts

- A1: one A13 discovery evidence record per immutable discovery observation. Chain and mint are copied exactly; case is never normalized.
- A2: one A13 evidence record per normalized metric. Numeric and text/address values retain units; missing/unavailable/not-reported/not-applicable remain explicit NULL values plus `value_status`. Pool identity and market scope are preserved.
- A6: one A13 evidence record per canonical candle. OHLCV, interval, market identity, raw-response reference and source record index are retained. The caller must supply `observed_at` from collection/raw-response provenance; candle open time is never substituted for observation time. Parser `unconfirmed` maps to A13 `unknown`, never to `closed`; `forming` remains ineligible.
- All projections calculate a deterministic A13 payload hash and preserve source, endpoint, run, raw response and normalization version.

## Snapshot approval and consumption

`assess_and_store_snapshot()` runs A13 record validation, persists append-only assessments, creates a deterministic governance decision, and persists that decision. `authorize_snapshot()` must be called by every downstream research consumer with the exact records it intends to expose, expected snapshot hash, persisted assessment hash, allowed policy versions and freshness policy. Lifecycle registration is mandatory by default; withdrawn and superseded snapshots are denied. Retention enforcement is separately enabled once production policy assignments exist. Warning-approved snapshots are denied by default unless the consumer explicitly opts in. Raw collector tables must not be exposed through an ungated path.

## Migrations

- 001–004: original v1.3.0 migration SQL retained unchanged.
- 005: append-only A1 discovery observations.
- 006: append-only A13 assessments and snapshot decisions.
- Runner checksum-checks all applied migrations and refuses checksum drift.

## Verification

Run from this directory:

```bash
python -m unittest discover -s tests -v
python -m compileall -q rooibos_collectors rooibos_a1 rooibos_a2 rooibos_a6 rooibos_a13 rooibos_db rooibos_integrations
```

No provider-specific Moby adapters, endpoint approvals, network client, credentials, Chart Engine wiring, agent runtime wiring, or trading capabilities are included. A6 MET fixture mapping remains blocked pending genuine fixture evidence. A13's production source policy registry remains unconfigured. This is a tested integration seam, not end-to-end production acceptance.

## Chart Engine gated reader (v1.5.0)

`rooibos_chart_engine.ChartSnapshotReader` is the controlled A13 read boundary for Chart Engine consumers. It calls `authorize_snapshot()` against the persisted A13 decision before returning any candle. It then independently verifies each OHLCV payload hash and checks exact chain/mint identity, interval, closed status, finite values, OHLC relationships, timestamp uniqueness and consecutive intervals. Raw-response IDs and source record indices remain attached to returned candles.

Example construction:

```python
from rooibos_chart_engine import ChartSnapshotReader

reader = ChartSnapshotReader(
    conn,
    allowed_policy_versions={"a13-governance/0.1.0"},
    allow_warnings=False,
)
chart_snapshot = reader.load(
    snapshot_id=snapshot_id,
    records=exact_a13_evidence_records,
    expected_snapshot_hash=persisted_snapshot_hash,
    expected_assessments_hash=persisted_assessments_hash,
    chain="solana",
    mint=exact_mint,
    interval="15m",
    expected_candles=100,
)
```

The caller must resolve the exact records belonging to the snapshot and pass those records to this reader. Do not provide Chart Engine code with direct access to raw collector tables or an alternative un-gated loader. The supplied v1.3.0 archive did not contain the Chart Engine v0.3.5 implementation, so this release provides and tests the integration boundary; it does not claim the historical Chart Engine runtime has been wired or deployed. A runtime integration must replace every existing direct OHLCV read path with this boundary and add a test proving no bypass remains.

## Chart Engine runtime integration preparation (v1.5.1)

The historical Chart Engine v0.3.5 source was not included in the collector package. Rather than invent its interfaces, v1.5.1 adds a concrete runtime integration contract, acceptance matrix and a heuristic static access-audit utility. These documents define the mandatory single A13-gated data path, deny conditions, bypass-removal requirements and compatibility tests. They do not claim runtime wiring is complete.

- `docs/CHART_ENGINE_RUNTIME_INTEGRATION.md`
- `docs/CHART_ENGINE_ACCEPTANCE_MATRIX.md`
- `tools/audit_chart_engine_access.py`

After the v0.3.5 source is available, run the audit against its actual analysis/runtime directory, review every finding, wire all entry points to `ChartSnapshotReader`, then execute the full matrix. The audit is heuristic and must be combined with runtime negative-path tests; a clean audit alone is not proof of enforcement.

Run the audit with:

```bash
python tools/audit_chart_engine_access.py path/to/chart_engine_runtime
```

## Agent runtime A13 integration preparation (v1.6.0)

This increment adds the Agent Runtime A13 Data Boundary & Integration Contract, a 34-case acceptance matrix, and a proposed machine-readable default-deny access policy. They define the mandatory `Agent Evidence Service -> A13 Read Gate -> persisted evidence` route, per-agent isolation, short-lived revocable grants, cache binding, blind-first-pass collaboration, controlled submissions, prompt-injection handling and strict paper-only boundaries.

The actual Alpha/Beta/Gamma runtime source and deployment topology were not supplied. Therefore this release is **specification only**: it does not implement an evidence service, OS identities, filesystem permissions, grants, revocation, agent orchestration, submission API or production policy deployment. Do not treat the JSON policy as active enforcement. Runtime integration requires source inspection and deployment-level tests from `docs/AGENT_RUNTIME_ACCEPTANCE_MATRIX.md`.

- `docs/AGENT_RUNTIME_A13_INTEGRATION.md`
- `docs/AGENT_RUNTIME_ACCEPTANCE_MATRIX.md`
- `config/agent_data_access_policy.json` (proposed, not deployed)


A13 lifecycle and retention governance is specified in `docs/A13_SNAPSHOT_LIFECYCLE_GOVERNANCE.md`. Migration 008 adds append-only publication/supersession/withdrawal, legal-hold, retention-policy and raw-evidence linkage records. Expiry does not delete evidence.


## Common A1/A2/A6 processing pipeline (incremental)

The shared collector framework now has provider-neutral processing entry points
for all three data families:

- `process_a6_raw_response()`: reads the immutable captured response, invokes
  an explicit provider decoder, normalises and persists A6 candles, then
  publishes through A13.
- `process_a2_raw_response()`: binds decoded metric inputs to captured
  provenance, normalises and persists A2 observations, then publishes through
  A13.
- `process_a1_raw_response()`: verifies decoded discovery-observation
  provenance, batch-persists A1 records atomically, then publishes through A13.

A1, A2 and A6 publication now share the same A13 transaction boundary:
assessment, decision, raw-evidence links, lifecycle registration and final
read-gate verification. The lifecycle registrar and read gate independently
bind each evidence record's run/source/endpoint identity to the immutable raw
response row, in addition to checking exact raw-response/source-index links.
A quarantined decision remains auditable but is not registered or exposed to
consumers. Parser/normalizer errors and provenance mismatches fail the run closed.

Collector completion is no longer an ordinary framework transition.
`transition(..., "completed")` is rejected; pipelines must supply the A13
GateGrant and exact evidence records to `complete_after_a13_publication()`.
That method re-runs the A13 read gate, verifies every record belongs to the
same collection run and that its raw response has matching run/source/endpoint
metadata, then marks the run complete inside the same database transaction.

Provider decoders are explicit injected functions. They only transform an
already-captured payload into the relevant provider-neutral input model; they
do not perform network requests. Endpoint allowlisting, request tickets and
raw capture remain the responsibility of `CollectorFramework`.

This is an implementation seam, not yet an approved production collector.
No Moby endpoint mapping, live network adapter, credential handling, or
production source policy is enabled. Synthetic end-to-end regression tests now
cover A1 and A2 capture, decoding, complete source-record coverage, provenance
binding, persistence, A13 assessment/publication and successful run completion.
A1 and A2 incomplete-source coverage is tested to fail before persistence or
publication. A6 has corresponding full-path publication and quarantine tests.
These are synthetic acceptance results only; genuine provider fixtures, source
mapping approval and deployment acceptance remain outstanding.
