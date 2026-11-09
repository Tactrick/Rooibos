# Changelog

## v1.6.0 — Agent Runtime A13 integration preparation

- Added the agent evidence-service and A13 read-gate integration contract.
- Specified per-agent process/workspace isolation, grant lifecycle, cache binding and fail-closed behaviour.
- Specified blind-first-pass, freeze-before-peer-review and controlled Research Library submission.
- Added prompt-injection, inert attachment, audit and paper-only capability requirements.
- Added a 34-case agent-runtime acceptance matrix.
- Added proposed default-deny machine-readable policy (`status: proposed_not_deployed`).
- No agent runtime source or deployment permissions were modified; no agent runtime implementation is claimed.


## v1.7.0 — A13 source registry governance

- Added versioned source/endpoint registry schema and an intentionally empty, proposed-not-approved default registry.
- Added fail-closed registry loader with approval status, timezone-aware validity and expiry handling.
- Added endpoint-specific dataset and chain scope enforcement in A13 record validation.
- Added source independence-group metadata and governance/evidence-review specification.
- Added tests for deny-all defaults, expiry, duplicate IDs, unsupported datasets, timestamps and scope violations.
- No source/endpoint is approved; no network, credentials, provider mappings or live collection are configured.


## v1.8.0 — A13 snapshot and evidence lifecycle governance

- Added migration 008 for append-only lifecycle events, hash-chained event history, versioned retention policies, immutable snapshot retention assignments and raw-response linkage.
- Added explicit register, supersede, withdraw and legal-hold operations with constrained state transitions.
- Added retention assessment; expiry can deny current research use but never deletes evidence. No production retention periods or purge process are configured.
- Made lifecycle registration mandatory by default in the A13 snapshot read gate; legacy/pre-008 snapshots require explicit review and registration.
- Added tests for lifecycle transitions, supersession, withdrawal, retention expiry, legal hold and append-only behaviour.
- Migrations 001–007 preserved; no deployment, provider collection or trading actions performed.
