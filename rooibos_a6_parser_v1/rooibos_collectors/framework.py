"""Fail-closed, provider-neutral collector run framework.

This module authorises and records collection work. It deliberately does not
perform network requests, hold credentials, or expose a generic HTTP client.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Mapping

from rooibos_db.database import _connect, initialize_database
from rooibos_a6.storage import store_raw_response


class CollectorPolicyError(PermissionError):
    """A collector, source or endpoint is not authorised."""


class InvalidRunTransition(ValueError):
    """A collection run lifecycle transition is invalid."""


@dataclass(frozen=True)
class EndpointPolicy:
    source_id: str
    endpoint_id: str
    endpoint_name: str
    method: str
    path: str
    allowed_parameter_keys: frozenset[str]
    validated: bool = False
    enabled: bool = True
    max_limit: int = 100


@dataclass(frozen=True)
class RequestTicket:
    request_id: str
    run_id: str
    endpoint_id: str
    request_sequence: int
    request_params: dict[str, Any]


_ALLOWED_TRANSITIONS = {
    "created": {"configured", "cancelled", "failed"},
    "configured": {"collecting", "cancelled", "failed"},
    "collecting": {"raw_captured", "partial", "incomplete", "failed", "cancelled"},
    "raw_captured": {"collecting", "parsed", "partial", "incomplete", "failed", "cancelled"},
    "parsed": {"normalised", "incomplete", "failed", "quarantined"},
    "normalised": {"validating", "incomplete", "failed", "quarantined"},
    "validating": {"completed", "partial", "incomplete", "failed", "quarantined"},
    "partial": {"collecting", "parsed", "normalised", "validating", "completed", "incomplete", "quarantined", "failed"},
    "incomplete": set(), "completed": set(), "failed": set(), "cancelled": set(), "quarantined": set(),
}


class CollectorFramework:
    def __init__(self, db_path: str | Path, endpoint_policies: list[EndpointPolicy] | None = None):
        self.db_path = Path(db_path)
        initialize_database(self.db_path)
        self._policies = {
            (p.source_id, p.endpoint_id): p for p in (endpoint_policies or [])
        }

    def create_run(
        self, *, run_id: str, collector_id: str, source_id: str, endpoint_id: str,
        collector_version: str, requested_scope: Mapping[str, Any], started_at: str | None = None,
    ) -> None:
        """Create a run only when its collector/source/endpoint are registered and enabled."""
        policy = self._require_policy(source_id, endpoint_id)
        if not policy.validated:
            raise CollectorPolicyError("Endpoint mapping has not passed validation; collection is disabled")
        conn = _connect(self.db_path)
        try:
            collector = conn.execute("SELECT is_enabled FROM collectors WHERE collector_id=?", (collector_id,)).fetchone()
            source = conn.execute("SELECT is_enabled FROM sources WHERE source_id=?", (source_id,)).fetchone()
            endpoint = conn.execute("""SELECT source_id,endpoint_name,method,path,is_enabled
                FROM source_endpoints WHERE endpoint_id=?""", (endpoint_id,)).fetchone()
            if not collector or not collector["is_enabled"]:
                raise CollectorPolicyError("Collector is missing or disabled")
            if not source or not source["is_enabled"]:
                raise CollectorPolicyError("Source is missing or disabled")
            if not endpoint or not endpoint["is_enabled"]:
                raise CollectorPolicyError("Endpoint is missing or disabled")
            actual = (endpoint["source_id"], endpoint["endpoint_name"], endpoint["method"], endpoint["path"])
            expected = (policy.source_id, policy.endpoint_name, policy.method, policy.path)
            if actual != expected:
                raise CollectorPolicyError("Database endpoint definition does not match the approved endpoint policy")
            conn.execute("""INSERT INTO collection_runs(
                run_id,collector_id,source_id,endpoint_id,collector_version,requested_scope_json,started_at,status
            ) VALUES (?,?,?,?,?,?,?,'created')""", (
                run_id,collector_id,source_id,endpoint_id,collector_version,
                json.dumps(requested_scope,sort_keys=True,separators=(",",":")),
                started_at or datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()
        finally:
            conn.close()

    def transition(self, run_id: str, new_status: str, *, error_summary: str | None = None) -> None:
        if new_status == "completed":
            raise InvalidRunTransition(
                "collector runs can complete only through complete_after_a13_publication()"
            )
        conn = _connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM collection_runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise KeyError(f"Unknown collection run: {run_id}")
            old = row["status"]
            if new_status not in _ALLOWED_TRANSITIONS.get(old, set()):
                raise InvalidRunTransition(f"Invalid collection run transition: {old} -> {new_status}")
            completed_at = datetime.now(timezone.utc).isoformat() if new_status in {
                "completed","incomplete","failed","cancelled","quarantined"
            } else None
            conn.execute("UPDATE collection_runs SET status=?,completed_at=COALESCE(?,completed_at),error_summary=? WHERE run_id=?",
                         (new_status,completed_at,error_summary,run_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_after_a13_publication(self, run_id: str, *, grant, records) -> None:
        """Complete a run only after independently re-authorizing its A13 grant.

        The grant is treated as a claim, not a bearer capability: the persisted
        decision, exact snapshot membership, content hash, lifecycle, raw links
        and record-to-raw-source coordinates are checked again inside the same
        write transaction that marks the run complete.
        """
        from rooibos_a13.read_gate import GateGrant, authorize_snapshot

        rows = list(records)
        if not isinstance(grant, GateGrant) or not rows:
            raise InvalidRunTransition("completion requires an A13 GateGrant and non-empty evidence records")
        if any(getattr(record, "run_id", None) != run_id for record in rows):
            raise InvalidRunTransition("A13 snapshot contains evidence from a different collection run")

        conn = _connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT status, source_id, endpoint_id FROM collection_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"Unknown collection run: {run_id}")
            if run["status"] != "validating":
                raise InvalidRunTransition(
                    f"A13 publication can complete only a validating run, not {run['status']!r}"
                )
            if any(
                (record.source_id, record.endpoint_id) != (run["source_id"], run["endpoint_id"])
                for record in rows
            ):
                raise InvalidRunTransition(
                    "A13 evidence source/endpoint does not match the collection run"
                )

            for record in rows:
                raw = conn.execute(
                    "SELECT run_id, source_id, endpoint_id FROM raw_responses WHERE raw_response_id=?",
                    (record.raw_response_id,),
                ).fetchone()
                if raw is None or (
                    raw["run_id"], raw["source_id"], raw["endpoint_id"]
                ) != (run_id, record.source_id, record.endpoint_id):
                    raise InvalidRunTransition(
                        "A13 evidence raw-response provenance does not belong to this collection run"
                    )

            verified = authorize_snapshot(
                conn,
                snapshot_id=grant.snapshot_id,
                records=rows,
                expected_snapshot_hash=grant.snapshot_hash,
                expected_assessments_hash=grant.assessments_hash,
                allowed_policy_versions={grant.policy_version},
                allow_warnings=(grant.dataset_status == "approved_with_warnings"),
                require_lifecycle=True,
            )
            if verified != grant:
                raise InvalidRunTransition("supplied A13 grant differs from freshly verified persisted authorization")

            completed_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE collection_runs SET status='completed', completed_at=?, error_summary=NULL WHERE run_id=?",
                (completed_at, run_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def authorise_request(self, run_id: str, params: Mapping[str, Any]) -> RequestTicket:
        """Create an auditable request ticket; this does not execute a network call."""
        conn = _connect(self.db_path)
        try:
            # Serialize sequence allocation and run-state validation. The
            # UNIQUE(run_id, request_sequence) constraint remains the final
            # integrity barrier, while BEGIN IMMEDIATE avoids routine races
            # between concurrent request authorizations for the same run.
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute("SELECT source_id,endpoint_id,status FROM collection_runs WHERE run_id=?", (run_id,)).fetchone()
            if not run:
                raise KeyError(f"Unknown collection run: {run_id}")
            policy = self._require_policy(run["source_id"], run["endpoint_id"])
            if not policy.validated or not policy.enabled:
                raise CollectorPolicyError("Endpoint is not validated and enabled")
            if run["status"] not in ("configured","collecting","raw_captured","partial"):
                raise InvalidRunTransition(f"Cannot issue request while run is {run['status']}")
            unknown = set(params) - policy.allowed_parameter_keys
            if unknown:
                raise CollectorPolicyError(f"Unapproved request parameters: {sorted(unknown)}")
            limit = params.get("limit")
            if limit is not None and (isinstance(limit,bool) or not isinstance(limit,int) or limit < 1 or limit > policy.max_limit):
                raise CollectorPolicyError(f"Request limit must be an integer from 1 to {policy.max_limit}")
            sequence = conn.execute("SELECT COUNT(*) FROM collection_requests WHERE run_id=?", (run_id,)).fetchone()[0]
            request_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""INSERT INTO collection_requests(request_id,run_id,request_sequence,request_params_json,requested_at)
                VALUES (?,?,?,?,?)""", (request_id,run_id,sequence,json.dumps(params,sort_keys=True,separators=(",",":")),now))
            conn.execute("UPDATE collection_runs SET request_count=request_count+1,status='collecting' WHERE run_id=?", (run_id,))
            conn.commit()
            return RequestTicket(request_id,run_id,policy.endpoint_id,sequence,dict(params))
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def capture_response(
        self, ticket: RequestTicket, *, raw_response_id: str, payload: Any,
        http_status: int | None = None, response_headers: Mapping[str, Any] | None = None,
        provider_timestamp: str | None = None, retrieved_at: str | None = None,
        content_type: str | None = "application/json", page_sequence: int | None = None,
    ) -> str:
        """Persist raw response before any parsing; update run capture state."""
        conn = _connect(self.db_path)
        try:
            request = conn.execute("""SELECT r.run_id,r.request_params_json,c.source_id,c.endpoint_id,c.status AS run_status
                FROM collection_requests r JOIN collection_runs c ON c.run_id=r.run_id
                WHERE r.request_id=?""", (ticket.request_id,)).fetchone()
            if not request or request["run_id"] != ticket.run_id or request["endpoint_id"] != ticket.endpoint_id:
                raise CollectorPolicyError("Request ticket provenance is invalid")
            request_params = json.loads(request["request_params_json"])
            source_id = request["source_id"]
            existing = conn.execute(
                """SELECT request_id FROM raw_responses WHERE raw_response_id=?""",
                (raw_response_id,),
            ).fetchone()
            already_captured = existing is not None
            if already_captured and existing["request_id"] != ticket.request_id:
                raise CollectorPolicyError("Raw response ID is already bound to a different request")
            if request["run_status"] != "collecting" and not already_captured:
                raise InvalidRunTransition("A new response can only be captured while the run is collecting")
        finally:
            conn.close()
        raw_hash = store_raw_response(
            self.db_path,raw_response_id=raw_response_id,run_id=ticket.run_id,source_id=source_id,
            endpoint_id=ticket.endpoint_id,request_id=ticket.request_id,payload=payload,
            request_params=request_params,retrieved_at=retrieved_at,provider_timestamp=provider_timestamp,
            http_status=http_status,response_headers=response_headers,content_type=content_type,
            page_sequence=page_sequence,
        )
        # store_raw_response enforces exact equality for an existing ID. An
        # exact retry must not increment response_count or regress a run that
        # has already advanced beyond raw capture.
        if already_captured:
            return raw_hash
        conn = _connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            # The run may have been cancelled or failed after the initial
            # ticket check and raw-response insert. Never resurrect a terminal
            # run by unconditionally writing raw_captured here.
            updated = conn.execute(
                """UPDATE collection_runs
                   SET response_count=(
                       SELECT COUNT(*) FROM raw_responses WHERE run_id=?
                   ),status='raw_captured'
                   WHERE run_id=? AND status IN ('collecting','raw_captured')""",
                (ticket.run_id, ticket.run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunTransition(
                    "Collection run left collecting state before response capture completed"
                )
            conn.execute(
                "UPDATE collection_requests SET response_status=? WHERE request_id=?",
                (http_status,ticket.request_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return raw_hash

    def _require_policy(self, source_id: str, endpoint_id: str) -> EndpointPolicy:
        policy = self._policies.get((source_id,endpoint_id))
        if policy is None:
            raise CollectorPolicyError("Endpoint is not explicitly allowlisted; default deny")
        name = policy.endpoint_name.lower()
        if any(term in name for term in ("swap","buy","sell","order","trade","wallet","portfolio","position")):
            raise CollectorPolicyError("Trading, wallet and portfolio endpoints are prohibited")
        if policy.method != "GET":
            raise CollectorPolicyError("Only explicitly approved GET research endpoints are supported")
        if not policy.path.startswith("/") or ".." in policy.path or "://" in policy.path:
            raise CollectorPolicyError("Endpoint path is invalid")
        return policy
