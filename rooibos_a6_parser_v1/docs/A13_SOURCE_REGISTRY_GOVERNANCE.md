# A13 Source & Endpoint Registry Governance v0.1

**Status: specification and local validation utility only. The supplied registry is empty and `proposed_not_approved`. No source or endpoint is approved for production collection.**

## Purpose

A13 currently receives a `source_policies` mapping from its caller. This workstream defines how that mapping is derived from a versioned, reviewable registry rather than from ad-hoc caller configuration. The registry is a quality/provenance allowlist; it is not a network endpoint catalogue, credential store, Moby tool mapping, or permission to collect.

## Registry identity

Each source has a stable `source_id`, human-readable display name and `independent_group`. Sources operated by the same provider, sharing infrastructure, or relying on the same upstream data should normally share an independence group. Different endpoint IDs do not establish independent corroboration.

Each endpoint entry declares:

- opaque `endpoint_id` (no URL or method is inferred);
- supported dataset types (`a1_discovery`, `a2_market`, `a6_ohlcv`);
- supported chains;
- evidence fields expected to be preserved.

Each approved source requires an approval reference, approver, approval timestamp and expiry. Approval must be scoped to the exact source, endpoints, dataset types and chains. Changes require a new registry version and review; no in-place silent expansion.

## Fail-closed lifecycle

`proposed_not_approved`, `suspended` and `retired` registries yield no active A13 source policies. Only registry status `approved` can yield policies. Expired sources are excluded. Unknown source IDs, endpoints, datasets or chains must be rejected upstream; the current v0.1 loader only constructs the source/endpoint allowlist and does not itself validate per-record chain/dataset scope. That scope enforcement is a required follow-on before production.

The registry loader is local-only: no network, credentials, provider calls or Moby integration. It does not make a source trustworthy merely because a human entered it. Approval requires evidence review.

## Evidence review required before approval

For each source/endpoint:

1. Identify the actual operator and the legal/product source identity.
2. Obtain genuine, lawfully captured response fixtures with timestamps and raw-response retention.
3. Verify endpoint semantics, supported chains, asset/pool identity and pagination behaviour.
4. Verify timestamp meaning, units, null/missingness semantics and update cadence.
5. Verify whether values are observed, estimated, aggregated or derived.
6. Check provenance completeness and whether historical snapshots can be retained.
7. Assess independence group and upstream dependencies.
8. Test malformed, partial, stale, duplicate and contradictory responses.
9. Record reviewer, evidence references, limitations, approval scope and expiry.
10. Re-run A13 acceptance tests after every mapping or policy version change.

## A1/A2/A6-specific review

- **A1:** exact chain + mint identity, no case folding; distinguish source-reported creation/first-seen claims from collector observation time; preserve raw record index and response reference.
- **A2:** metric definition, unit, quote currency, pool/token scope, measurement kind, window and observation time must be explicit. No averaging or source winner selection. Same-provider endpoints are not independent corroboration.
- **A6:** candle open timestamp units, interval, closure semantics, market/pool identity, volume unit, ordering and pagination must be proven from genuine fixtures. Unknown/unconfirmed candles cannot be promoted to closed.

## Independence and conflicts

`independent_group` is a provenance grouping hint, not a truth score. Cross-source comparisons must use distinct independent groups and matched asset, metric, unit, market scope, window and time. Preserve disagreements. Do not average or select a winner automatically. A13 currently accepts externally supplied conflict record IDs; a source-comparison service is still required to detect conflicts and populate them reliably.

## Approval authority and audit

Production approval should require a named human reviewer and a version-controlled change record. Registry updates must be append-only in release history; retain the previous registry and its hash. The local v0.1 loader validates structure and expiry but does not provide cryptographic signatures, reviewer authentication, deployment enforcement, or audit-log persistence.

## Current status and next gates

- Empty registry: no production sources approved.
- No endpoint URL, Moby tool, credential or network adapter is configured.
- No live collection is authorised by this package.
- Registry hash persistence, authenticated human approval, and deployment-time policy pinning remain implementation requirements.
- A6 MET source mapping remains blocked pending genuine fixtures.
