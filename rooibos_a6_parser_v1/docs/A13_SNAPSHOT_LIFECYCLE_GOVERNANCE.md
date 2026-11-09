# A13 Snapshot & Evidence Lifecycle Governance — v0.1

**Implementation status:** Local implementation and tests in pipeline v1.8.0. Not deployed. No retention duration has been approved for production. No evidence deletion or automated purge is implemented.

## 1. Core invariants

1. A13 approval and lifecycle publication are separate events. A snapshot is not consumable until it has a persisted approval decision **and** an append-only `registered` lifecycle event.
2. Evidence, raw responses, record assessments, snapshot decisions, lifecycle events, retention policies, assignments and raw-response links are immutable in the database schema.
3. Corrections create new observations and a new snapshot. They never rewrite the old record or decision.
4. Supersession preserves the old snapshot and links it to a replacement. The old snapshot becomes unavailable to current research consumers; the replacement must have its own persisted A13 approval.
5. Withdrawal blocks current research use without destroying the evidence or its history.
6. Retention expiry is not deletion. This release reports expiry and can deny current research use; it does not delete raw evidence or snapshots.
7. Legal hold preserves linked evidence from any future purge process. It does not reactivate a withdrawn or superseded snapshot.
8. A governance policy change does not rewrite historical decisions. Consumers explicitly allow policy versions; withdrawn/superseded snapshots remain denied regardless of version.

## 2. Lifecycle states and transitions

- `registered`: approved snapshot published for research use.
- `superseded`: replaced by a separately approved snapshot of the same dataset type.
- `withdrawn`: explicitly removed from current research use due to invalidated source/evidence, governance concern or correction.
- `legal_hold_placed` / `legal_hold_released`: append-only events that control preservation status, not research approval.

Allowed transitions are enforced by the lifecycle service. Terminal states `superseded` and `withdrawn` cannot be reversed. To resume research, create and approve a new snapshot. Every event carries actor, reason, effective timestamp, related snapshot and a hash chained to the preceding event. The read path verifies the full chain.

## 3. Corrections, conflicts and versioning

- Never update a raw response, normalized observation, A13 assessment or decision to correct it.
- Preserve each source variant and its provenance. Do not merge or average conflicting observations to create a canonical fact.
- Recollect or renormalize into new immutable observations with a new run/normalization version as appropriate.
- Reassess the new records under the current source registry and A13 policy.
- Publish a new snapshot and append a `superseded` event to the prior snapshot plus a `registered` event for the replacement.
- If an earlier claim becomes unreliable without a replacement, append a `withdrawn` event. Retain the original evidence for audit and reproducibility.

## 4. Raw evidence linkage and retention

`a13_snapshot_raw_links` records the raw response IDs supporting a snapshot. Publication requires at least one raw-response link; `register_snapshot_for_records()` verifies exact record membership and derives the links directly from each record. Links are immutable and foreign-key constrained to `raw_responses`. This makes the raw evidence set discoverable from a snapshot without copying or altering provider payloads.

Retention policies are immutable, versioned records. Each policy requires:
- a policy ID and sequential version;
- dataset type;
- positive retention duration in days;
- timezone-aware effective timestamp;
- approval reference and creator.

A snapshot receives one immutable retention-policy assignment. The expiry is computed from the A13 decision timestamp plus the assigned duration. A policy change creates a new policy version; it does not silently rewrite previous assignments. Any transition of existing assignments requires a separately reviewed, append-only reassignment design and is not supported by this release.

No production retention durations are set by default. No duration should be guessed. Before deployment, the owner must approve retention periods for raw responses, normalized records, assessments, snapshots and audit events, taking account of provider terms, legal obligations, operational requirements and reproducibility needs.

When retention is enforced at the read gate, missing policy assignment or an expired period must deny access. Legal hold blocks deletion but does not extend research-use validity. This implementation deliberately sets `deletion_permitted=False` in every retention assessment. Physical purge, cryptographic erasure, backup expiry and legal-hold purge release require a separate reviewed implementation.

## 5. Read-gate requirements

`authorize_snapshot()` now requires lifecycle registration by default. It denies missing, invalid, superseded or withdrawn lifecycle state. The consumer may additionally enable retention enforcement; when enabled, missing assignment and expiry must fail closed. Warning-approved snapshots still require explicit consumer opt-in.

All Chart Engine and future Agent Evidence Service calls must use this gate. This library change does not establish OS/database isolation or prove there are no alternative direct-read paths. Runtime integration and bypass audits remain outstanding.

## 6. Migration and compatibility

Migration 008 is additive. Migrations 001–007 and their checksums are preserved. Existing snapshots created before migration 008 have no lifecycle registration and are therefore denied by the default read gate until explicitly reviewed and registered. Registration is not automatic: an operator must verify that the persisted A13 decision is approved and that the evidence set is appropriate for publication.

## 7. Operational acceptance before deployment

- Review and approve retention periods and legal basis/provider terms.
- Decide which record classes need distinct retention schedules.
- Verify raw-response links against actual collector runs and payload hashes.
- Test registration, supersession, withdrawal, legal holds, expired policy and missing policy.
- Verify hash-chain tampering detection and append-only triggers.
- Test backup/restore preserves lifecycle events, retention assignments and raw links.
- Ensure Chart Engine and Agent Evidence Service set retention enforcement according to the approved production policy.
- Confirm no agent or collector can mutate lifecycle/retention tables or call direct deletion paths.
- No automated deletion until separate purge design, approvals, recovery tests and legal review are complete.
