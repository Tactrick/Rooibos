"""Deterministic snapshot gate. Conflicts and incomplete inputs fail closed."""
from .models import RecordAssessment, SnapshotDecision, Finding, canonical_hash, A13ValidationError

def decide_snapshot(snapshot_id: str, dataset_type: str, assessments: list[RecordAssessment], *,
                    decided_at: str, allow_warnings: bool = False, conflict_record_ids: set[str] | None = None,
                    expected_record_ids: set[str] | None = None,
                    snapshot_hash: str = "") -> SnapshotDecision:
    if not snapshot_id or not dataset_type or not assessments:
        raise A13ValidationError("snapshot_id, dataset_type and at least one assessment are required")
    ids = [a.record_id for a in assessments]
    if len(ids) != len(set(ids)):
        raise A13ValidationError("duplicate record IDs in snapshot input")
    conflicts = conflict_record_ids or set()
    expected = expected_record_ids or set(ids)
    findings = []
    missing = expected - set(ids)
    if missing:
        findings.append(Finding("snapshot_incomplete", "error", f"{len(missing)} expected records have no assessment."))
    if conflicts:
        findings.append(Finding("source_conflict", "error", f"{len(conflicts)} record(s) are flagged as conflicting; preserve and quarantine rather than blend."))
    if any(a.status in ("rejected", "incomplete", "duplicate", "conflicting") for a in assessments):
        findings.append(Finding("record_not_approvable", "error", "At least one record is rejected, incomplete, duplicate or conflicting."))
    warning_records = [a for a in assessments if a.status == "valid_with_warnings"]
    if warning_records and not allow_warnings:
        findings.append(Finding("warnings_not_accepted", "error", "Warnings require an explicit snapshot policy allowance."))
    for a in assessments:
        findings.extend(a.findings)
    if findings and any(f.severity == "error" for f in findings):
        status = "quarantined"
    elif warning_records:
        status = "approved_with_warnings"
    else:
        status = "approved_for_research"
    digest = canonical_hash([{"record_id": a.record_id, "status": a.status, "findings": [f.__dict__ for f in a.findings], "validator_version": a.validator_version} for a in assessments])
    return SnapshotDecision(snapshot_id, dataset_type, tuple(ids), status, digest, tuple(findings), decided_at,
                            snapshot_hash=snapshot_hash)
