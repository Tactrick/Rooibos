"""Fail-closed loader for the A13 source/endpoint approval registry.

This module validates local policy data only. It performs no network access and
never infers provider endpoint semantics or approves a source automatically.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SourcePolicy

REGISTRY_SCHEMA_VERSION = "1.0.0"


class SourceRegistryError(ValueError):
    pass


def _parse_time(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise SourceRegistryError(f"Invalid ISO-8601 timestamp: {value!r}") from exc
    if dt.tzinfo is None:
        raise SourceRegistryError("Registry timestamps must include a timezone")
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class ApprovedEndpoint:
    endpoint_id: str
    dataset_types: tuple[str, ...]
    chains: tuple[str, ...]
    evidence_fields: tuple[str, ...]


@dataclass(frozen=True)
class ApprovedSource:
    source_id: str
    display_name: str
    independent_group: str
    approval_reference: str
    approved_by: str
    approved_at: str
    expires_at: str
    endpoints: tuple[ApprovedEndpoint, ...]


@dataclass(frozen=True)
class SourceRegistry:
    registry_version: str
    policy_version: str
    status: str
    sources: tuple[ApprovedSource, ...]

    def source_policies(self, *, now: str) -> dict[str, SourcePolicy]:
        """Return only currently approved, unexpired source/endpoint policies."""
        current = _parse_time(now)
        result: dict[str, SourcePolicy] = {}
        if self.status != "approved":
            return result
        for source in self.sources:
            if current >= _parse_time(source.expires_at):
                continue
            result[source.source_id] = SourcePolicy(
                source_id=source.source_id,
                approved_endpoints=tuple(e.endpoint_id for e in source.endpoints),
                active=True,
                independent_group=source.independent_group,
                allowed_dataset_types=tuple(sorted({d for e in source.endpoints for d in e.dataset_types})),
                allowed_chains=tuple(sorted({c for e in source.endpoints for c in e.chains})),
                endpoint_scopes={e.endpoint_id: {"dataset_types": e.dataset_types, "chains": e.chains}
                                 for e in source.endpoints},
            )
        return result


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceRegistryError(f"{field} must be a non-empty string")
    return value


def load_registry_data(data: dict[str, Any]) -> SourceRegistry:
    if not isinstance(data, dict):
        raise SourceRegistryError("Registry root must be an object")
    if data.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise SourceRegistryError("Unsupported registry schema_version")
    registry_version = _nonempty(data.get("registry_version"), "registry_version")
    policy_version = _nonempty(data.get("policy_version"), "policy_version")
    status = data.get("status")
    if status not in {"proposed_not_approved", "approved", "suspended", "retired"}:
        raise SourceRegistryError("Invalid registry status")
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list):
        raise SourceRegistryError("sources must be an array")
    seen_sources: set[str] = set()
    sources: list[ApprovedSource] = []
    for i, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            raise SourceRegistryError(f"sources[{i}] must be an object")
        sid = _nonempty(raw.get("source_id"), f"sources[{i}].source_id")
        if sid in seen_sources:
            raise SourceRegistryError(f"Duplicate source_id: {sid}")
        seen_sources.add(sid)
        endpoints_raw = raw.get("endpoints")
        if not isinstance(endpoints_raw, list) or not endpoints_raw:
            raise SourceRegistryError(f"{sid}: endpoints must be a non-empty array")
        endpoints: list[ApprovedEndpoint] = []
        seen_endpoints: set[str] = set()
        for j, ep in enumerate(endpoints_raw):
            if not isinstance(ep, dict):
                raise SourceRegistryError(f"{sid}.endpoints[{j}] must be an object")
            eid = _nonempty(ep.get("endpoint_id"), f"{sid}.endpoint_id")
            if eid in seen_endpoints:
                raise SourceRegistryError(f"{sid}: duplicate endpoint_id {eid}")
            seen_endpoints.add(eid)
            datasets, chains, fields = ep.get("dataset_types"), ep.get("chains"), ep.get("evidence_fields")
            if not isinstance(datasets, list) or not datasets or not set(datasets) <= {"a1_discovery", "a2_market", "a6_ohlcv"}:
                raise SourceRegistryError(f"{sid}/{eid}: invalid dataset_types")
            if not isinstance(chains, list) or not chains or any(not isinstance(x, str) or not x.strip() for x in chains):
                raise SourceRegistryError(f"{sid}/{eid}: chains must be non-empty strings")
            if not isinstance(fields, list) or not fields or any(not isinstance(x, str) or not x.strip() for x in fields):
                raise SourceRegistryError(f"{sid}/{eid}: evidence_fields must be non-empty strings")
            endpoints.append(ApprovedEndpoint(eid, tuple(datasets), tuple(chains), tuple(fields)))
        approved_at = _nonempty(raw.get("approved_at"), f"{sid}.approved_at")
        expires_at = _nonempty(raw.get("expires_at"), f"{sid}.expires_at")
        if _parse_time(expires_at) <= _parse_time(approved_at):
            raise SourceRegistryError(f"{sid}: expires_at must be after approved_at")
        source = ApprovedSource(
            sid,
            _nonempty(raw.get("display_name"), f"{sid}.display_name"),
            _nonempty(raw.get("independent_group"), f"{sid}.independent_group"),
            _nonempty(raw.get("approval_reference"), f"{sid}.approval_reference"),
            _nonempty(raw.get("approved_by"), f"{sid}.approved_by"),
            approved_at,
            expires_at,
            tuple(endpoints),
        )
        sources.append(source)
    if status == "approved":
        for source in sources:
            _parse_time(source.approved_at)
            if not source.approval_reference or not source.approved_by:
                raise SourceRegistryError("Approved sources require an approval reference and approver")
    return SourceRegistry(registry_version, policy_version, status, tuple(sources))


def load_registry(path: str | Path) -> SourceRegistry:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceRegistryError(f"Unable to load source registry: {exc}") from exc
    return load_registry_data(data)
