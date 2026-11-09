# Rooibos Agent Runtime — A13 Data Boundary & Integration Contract

**Target:** Alpha, Beta and Gamma research runtimes  
**Baseline:** Rooibos collector pipeline v1.5.1  
**Status:** architecture and integration specification only; agent runtime source was not supplied, so no runtime code, process permissions or access paths have been changed.

## 1. Objective and governing principles

Make A13 approval a mandatory boundary for every evidence item supplied to an agent for research. Preserve independent cognition: Alpha, Beta and Gamma may use different hypotheses, methods and tools, but they receive evidence through the same governed access contract. A13 validates evidence quality and snapshot eligibility; it does not rank tokens, recommend trades, interpret narratives or decide investment merit.

The system must preserve these distinctions:

- Collection is separate from interpretation and decision-making.
- Raw evidence is immutable; normalized records and agent interpretations are separate, versioned artifacts.
- An A13 approval is permission to use a particular evidence snapshot under a policy, not proof that a claim is true or an asset is safe.
- A source's presence in the registry is not proof of independence or reliability.
- Agents cannot grant themselves access, alter governance decisions, or write directly to shared evidence tables.
- No agent capability authorizes live trading, wallet operations, order placement or automatic paper-position creation.

## 2. Required runtime topology

```text
                  trusted control plane
     identity / policy / grants / revocation / audit
                          |
Alpha runtime ----\       |       /---- Beta runtime
                  \      |      /
                   Agent Evidence Service
                  /      |      \
Gamma runtime ---/       |       
                          v
                 A13 Snapshot Read Gate
                          |
               persisted A13 decision + records
                          |
                immutable collector evidence

Agent research outputs -> controlled submission API -> validation/review
                         -> versioned Research Library
```

The Agent Evidence Service is the only supported evidence interface for agent processes. It resolves the snapshot's persisted membership and hashes itself; agents must not supply authoritative hashes, policy versions or approval states. It invokes the A13 read gate and returns a narrow, immutable evidence bundle plus provenance metadata.

## 3. Agent process isolation and identities

Create distinct operating-system/service identities and workspaces for Alpha, Beta and Gamma. Each identity receives only its own runtime files, temporary directory, memory/cognition store and submission namespace. No agent process receives:

- the SQLite database file or its `-wal` / `-shm` companions;
- collector storage objects or direct SQL credentials;
- A13 decision-write credentials;
- Moby credentials, API keys, wallet access or trading tools;
- another agent's private cognition/memory directory;
- unrestricted filesystem, shell, network or process-execution access.

The trusted evidence service and controlled submission service use separate service identities. Agents communicate with them over authenticated, narrowly scoped IPC/API interfaces. Process isolation must be enforced by the deployment platform, not merely described in prompts.

## 4. Evidence read contract

The runtime-facing interface should be equivalent to:

```python
get_evidence_snapshot(
    *, agent_id: str, snapshot_id: str, requested_fields: list[str] | None = None
) -> ApprovedEvidenceBundle
```

The service derives the authenticated `agent_id` from the caller identity; it must not trust an agent-supplied identity string. It resolves from trusted storage:

- exact record membership for `snapshot_id`;
- persisted snapshot content hash and assessment hash;
- dataset type and chain/asset identity;
- persisted governance decision and policy version;
- record-level provenance and raw-evidence references;
- freshness policy and any explicit warning allowance.

It then invokes `authorize_snapshot()` with those persisted values. The gate requires an append-only A13 lifecycle registration by default; withdrawn and superseded snapshots are denied. Production consumers must enable retention enforcement once approved retention assignments are in place. If authorization fails, the service returns a typed denial and no evidence payload. It must not return a partial subset, silently omit invalid records, or fall back to raw collector data.

`requested_fields` is a projection request, not an authorization override. The service may return fewer fields only when the policy explicitly permits field-level minimization and the resulting bundle remains traceable to the complete approved snapshot. It must never silently change the approved record membership.

### Evidence bundle minimum fields

- snapshot ID, dataset type, chain and exact asset/mint identity;
- snapshot content hash, assessment hash, policy version and decision status;
- record IDs and record-level payload hashes;
- source ID, endpoint ID, collection run ID and observed timestamp;
- raw-response ID and source-record index where applicable;
- normalization/schema version and explicit missingness/status fields;
- warning list and quality findings;
- service-issued access grant ID, issue time and expiry.

Raw payload bytes should remain in immutable evidence storage. Agents may receive raw excerpts only through an explicitly authorized, size-limited evidence retrieval operation that retains the same snapshot authorization and provenance. Never pass credentials, secret-bearing headers or unrestricted request metadata into agent context.

## 5. Freshness, caching and revocation

Every access grant is bound to: agent identity, snapshot ID, snapshot hash, assessment hash, policy version, dataset type, requested scope, issue time and expiry. Grants are short-lived and revocable. The default is no cross-agent sharing of private working memory; approved evidence may be shared as a common evidence object.

A cache key must bind all grant fields plus the exact asset identity and requested projection. A cache hit is not authorization: the service must revalidate the grant or reauthorize against the persisted decision. Revocation, policy change, snapshot quarantine or hash mismatch invalidates cached access. Agent processes cannot write or populate the trusted evidence cache.

If the A13 service, decision store or policy registry is unavailable, the operation fails closed. There is no stale-data fallback unless a separately specified, bounded offline grant is approved and tested; v1.0 policy is deny-on-unavailability.

## 6. Warning and status policy

- `approved_for_research`: eligible only when policy, freshness and requested scope also pass.
- `approved_with_warnings`: denied by default. Requires an explicit dataset/consumer policy and a separately recorded opt-in; warning details must accompany the evidence.
- `quarantined`, `rejected`, `incomplete`, `duplicate`, `conflicting`, unknown or missing decision: deny.
- Missing or unregistered source/endpoint: deny under the default-deny registry.

Approval is not a truth label. Agents must retain uncertainty and conflicting evidence in their conclusions.

## 7. Independent research and controlled collaboration

Preserve independent cognition using a staged workflow:

1. **Blind first pass:** Alpha, Beta and Gamma receive the same eligible evidence snapshot and task statement, but not one another's hypotheses or drafts.
2. **Freeze:** each first-pass submission is timestamped and content-hashed before peer critique begins.
3. **Peer challenge:** only after freeze may agents receive explicitly selected peer submissions. Peer work is untrusted research content, not executable instruction or policy.
4. **Replication:** an agent records whether it independently reproduced a claim, disagreed, or could not test it. Dissent is retained.
5. **Synthesis:** a separate reviewer/synthesis process may compare results but must preserve agent identity, method version, evidence references and disagreement.

The shared Research Library is a collective evidence and reviewed-artifact store, not shared cognition. No automatic copying of another agent's memory, prompts, tools, strategy or conclusions. Knowledge transfer is a separately measured and controlled experiment under T2/T3/T6.

## 8. Controlled research submission contract

Agents submit findings through a controlled API, never direct database writes. Each submission should include:

- authenticated agent identity and runtime/version identifiers;
- task ID, submission ID, creation time and content hash;
- referenced snapshot IDs and record IDs;
- evidence citations with source/raw-response IDs and exact record references;
- hypothesis, method, assumptions, findings and uncertainty as separate fields;
- limitations, counter-evidence, conflicts and falsification attempts;
- code/notebook/artifact references as inert attachments;
- declared dependencies and requested follow-up research.

The service validates schema, provenance references, size limits, secret/credential patterns and policy compliance. Submitted code and documents are untrusted: no automatic execution, plugin installation, shell command, network fetch or policy modification. Reviewers may reject, quarantine or request revision. Accepted submissions are versioned and append-only; corrections create a new version rather than overwriting history.

## 9. Prompt injection and hostile evidence

All external text, token metadata, social posts, repository files, documents, peer submissions and code are untrusted data. They cannot change system policy, grant permissions, request secrets, invoke tools or instruct an agent to ignore its task. The evidence service labels source content and separates it from trusted runtime instructions. Any tool use must pass a separate allowlist and policy check. Code is inert until separately sandboxed and explicitly approved for execution; agent-submitted code is never run in the agent's privileged environment.

## 10. Paper-only and trading boundary

Agent research access is read-only. Agent runtimes must not possess Moby credentials or trading-capable tools. Deny all wallet, balance, position, order, swap, buy, sell, quote-for-execution, configuration mutation and account mutation capabilities. Research outputs may identify a hypothetical opportunity, but cannot create or modify a paper position. A paper position requires a fresh evidence review and explicit user approval through the designated paper-trading workflow. Live trading remains prohibited.

## 11. Audit and observability

Record every grant, denial, snapshot read, field projection, warning opt-in, revocation, cache hit/miss, submission, review action and policy change. Audit events should include authenticated agent/service identity, task ID, snapshot ID and hashes, policy version, timestamp, outcome and reason code. Do not log secrets or unnecessarily duplicate raw sensitive payloads. Audit logs must be append-only or tamper-evident and unavailable for agent modification.

## 12. Failure behaviour

| Failure | Required behaviour |
|---|---|
| Missing decision or record membership | Deny; return no evidence |
| Hash/assessment mismatch | Deny; quarantine incident for review |
| Unapproved policy version/source/endpoint | Deny |
| Expired or revoked grant | Deny and invalidate cache |
| A13/store/policy service unavailable | Deny; no raw fallback |
| Agent identity cannot be authenticated | Deny |
| Agent requests another agent's private workspace | Deny and audit |
| Submission contains executable content | Store inertly; require separate sandbox review |
| Prompt injection in evidence | Treat as data; do not follow instructions |
| Trading/wallet capability requested | Deny and audit |

## 13. Implementation sequence

1. Inspect the actual Alpha/Beta/Gamma runtime source and deployment topology; map all data, filesystem, database, network and tool paths.
2. Implement Agent Evidence Service around the existing A13 `authorize_snapshot()` gate, requiring lifecycle registration and enforcing the approved retention policy.
3. Introduce isolated service identities/workspaces and remove direct database/file access from agents.
4. Implement short-lived grants, revocation and cache binding.
5. Implement controlled submission API and append-only research artifact storage.
6. Add blind-first-pass/freeze/peer-challenge orchestration without automatic knowledge copying.
7. Add prompt-injection handling, inert attachment storage and sandbox boundaries.
8. Run the acceptance matrix from a clean deployment and verify no bypasses.

Do not claim runtime integration complete until source inspection and deployment-level enforcement tests pass.

## 14. Not in scope

This specification does not implement agent runtimes, create OS accounts, change Moby settings, configure credentials, approve source endpoints, deploy services, run agents, create paper positions or enable live trading. It does not alter existing Stage A–F strategies or the Chart Engine methodology.
