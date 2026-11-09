"""A13 independent evidence-quality and snapshot-governance contracts."""
from dataclasses import dataclass, asdict
from typing import Any, Mapping, Optional, Sequence
import hashlib, json

STATUSES = {"valid", "valid_with_warnings", "rejected", "incomplete", "duplicate", "conflicting"}
DATASET_STATUSES = {"approved_for_research", "approved_with_warnings", "quarantined", "rejected"}

@dataclass(frozen=True)
class EvidenceRecord:
    record_id: str
    dataset_type: str              # a1_discovery | a2_market | a6_ohlcv
    chain: str
    mint: str
    source_id: str
    endpoint_id: str
    run_id: str
    raw_response_id: str
    observed_at: str
    normalization_version: str
    content_hash: str
    payload: Mapping[str, Any]
    source_record_index: Optional[int] = None

@dataclass(frozen=True)
class SourcePolicy:
    source_id: str
    approved_endpoints: Sequence[str]
    active: bool = True
    independent_group: Optional[str] = None
    allowed_dataset_types: Sequence[str] = ()
    allowed_chains: Sequence[str] = ()
    endpoint_scopes: Optional[Mapping[str, Mapping[str, Sequence[str]]]] = None

@dataclass(frozen=True)
class Finding:
    code: str
    severity: str               # error | warning
    message: str

@dataclass(frozen=True)
class RecordAssessment:
    record_id: str
    status: str
    findings: tuple[Finding, ...]
    assessed_at: str
    validator_version: str = "a13-validator/0.1.0"

@dataclass(frozen=True)
class SnapshotDecision:
    snapshot_id: str
    dataset_type: str
    record_ids: tuple[str, ...]
    dataset_status: str
    assessments_hash: str
    findings: tuple[Finding, ...]
    decided_at: str
    policy_version: str = "a13-governance/0.1.0"
    snapshot_hash: str = ""

class A13ValidationError(ValueError):
    pass

def canonical_hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

def to_dict(value: Any) -> dict:
    return asdict(value)
