# Agent Runtime + A13 Acceptance Matrix

**Status:** required test plan; not executed because Alpha/Beta/Gamma runtime source and deployment environment are not present in this package.

| ID | Area | Test | Expected result | Evidence required |
|---|---|---|---|---|
| AR01 | Identity | Unauthenticated client requests a snapshot | Denied; no payload | Service log + test assertion |
| AR02 | Identity | Agent A claims to be Agent B in request body | Authenticated identity wins; cross-agent scope denied | Identity binding test |
| AR03 | A13 | Snapshot has no persisted decision | Denied; no partial payload | Negative integration test |
| AR04 | A13 | Snapshot is quarantined/rejected/incomplete | Denied | Negative integration test |
| AR05 | A13 | Snapshot content hash is tampered | Denied | Tamper test |
| AR06 | A13 | Assessment hash or membership differs | Denied | Tamper/membership test |
| AR07 | A13 | Policy version is not allowlisted | Denied | Policy test |
| AR08 | A13 | Source or endpoint is unregistered | Denied | Registry test |
| AR09 | Warnings | Warning-approved snapshot without explicit opt-in | Denied | Default-policy test |
| AR10 | Warnings | Warning opt-in not present in trusted consumer policy | Denied | Policy provenance test |
| AR11 | Freshness | Snapshot exceeds max age | Denied | Clock-controlled test |
| AR12 | Freshness | Grant has expired or been revoked | Denied; cache invalidated | Grant lifecycle test |
| AR13 | Cache | Cache key omits policy/hash/agent scope | Test must fail; no cross-scope reuse | Cache-key test |
| AR14 | Cache | Snapshot quarantined after cache population | Subsequent read denied | Revocation/invalidation test |
| AR15 | Isolation | Agent process attempts to open DB, WAL or SHM | OS denies access | Deployment permission test |
| AR16 | Isolation | Agent accesses another agent's private workspace | OS/service denies; audit event | Deployment test |
| AR17 | Isolation | Agent attempts direct A13 decision write | Denied | DB/service permission test |
| AR18 | Isolation | Agent attempts raw collector table read | Denied; no fallback | Runtime negative test |
| AR19 | Provenance | Approved evidence is returned | Every item retains source/run/raw-response/index/hash | Bundle schema test |
| AR20 | Missingness | Explicit missing/unavailable values supplied | Status preserved; not coerced to zero | Serialization test |
| AR21 | Independence | Alpha/Beta/Gamma first-pass execution | No peer drafts visible before freeze | Orchestration test + access logs |
| AR22 | Independence | First-pass submission frozen | Timestamp and content hash immutable | Submission store test |
| AR23 | Collaboration | Peer critique begins before all required freezes | Blocked | Workflow state test |
| AR24 | Collaboration | Peer submission contains instruction/prompt injection | Treated as untrusted content | Red-team fixture test |
| AR25 | Submission | Agent submits finding with unknown evidence IDs | Rejected or quarantined | API validation test |
| AR26 | Submission | Agent submits secret-like material | Blocked/redacted under policy; event audited | Secret scanning test |
| AR27 | Submission | Agent submits executable attachment | Stored inertly; never auto-executed | Attachment handling test |
| AR28 | Audit | Successful and denied reads | Both produce tamper-evident audit events | Audit verification |
| AR29 | Availability | A13 service or policy store unavailable | Deny; no stale/raw fallback | Fault injection |
| AR30 | Trading boundary | Agent requests wallet/order/swap/live-trade capability | No such tool/credential available; denied and audited | Tool inventory + negative test |
| AR31 | Paper boundary | Agent attempts to create a paper position | Denied; explicit user approval workflow required | Workflow authorization test |
| AR32 | Recovery | Restart/recovery after revocation | Revocation persists; no cached grant reuse | Recovery test |
| AR33 | Reproducibility | Same approved snapshot and runtime version replayed | Evidence bundle/hash stable; access events distinct | Replay test |
| AR34 | Full system | Full suite from clean package/deployment | All tests pass; no direct access bypass | CI logs + deployment report |

## Release gate

Acceptance requires all applicable tests to pass in the real runtime/deployment, with no unresolved high-severity bypass. A clean static source scan is insufficient by itself. OS-level permissions, process isolation, tool inventories, database access denial and fault-injection tests are mandatory. Record each test's environment, build hash, policy version, timestamp and result.

| AR-LC1 | Lifecycle | Approved snapshot lacks registered lifecycle event | Denied | v1.8.0 lifecycle gate |
| AR-LC2 | Lifecycle | Snapshot is superseded or withdrawn | Denied | v1.8.0 lifecycle gate |
| AR-LC3 | Retention | Retention enforced without assignment or after expiry | Denied; no deletion | v1.8.0 retention gate |
