"""A1 request-ticket policy stub. No network client and no Moby calls are provided."""
from dataclasses import dataclass

CANDIDATE_ENDPOINTS = frozenset({
    "get_screener_leaderboard", "get_degen_leaderboard", "get_screener_groups",
    "get_group_leaderboard", "get_launchpads", "get_market_movers", "search_tokens"
})

@dataclass(frozen=True)
class EndpointApproval:
    endpoint_id: str
    source_id: str
    approved: bool = False
    schema_verified: bool = False
    provenance_verified: bool = False

class A1PolicyError(PermissionError):
    pass

def issue_request_ticket(approval: EndpointApproval, *, chain: str, purpose: str) -> dict:
    if approval.endpoint_id not in CANDIDATE_ENDPOINTS:
        raise A1PolicyError("endpoint is not in the A1 candidate allowlist")
    if not (approval.approved and approval.schema_verified and approval.provenance_verified):
        raise A1PolicyError("endpoint remains blocked pending explicit schema, provenance and policy approval")
    if purpose != "token_discovery":
        raise A1PolicyError("A1 permits token discovery only")
    if not chain:
        raise A1PolicyError("chain is required")
    return {"source_id": approval.source_id, "endpoint_id": approval.endpoint_id,
            "chain": chain, "purpose": purpose, "network_access": False}
