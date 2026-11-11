"""Provider-neutral A1 token discovery models. No network or trading functionality."""
from dataclasses import dataclass, asdict
from typing import Any, Mapping, Optional
import hashlib, json, re

class A1ValidationError(ValueError):
    pass

@dataclass(frozen=True)
class DiscoveryContext:
    chain: str
    mint: str
    source_id: str
    endpoint_id: str
    run_id: str
    raw_response_id: str
    source_record_index: int
    observed_at: str
    normalization_version: str = "a1-normalizer/0.1.0"
    source_record_count: int | None = None  # full provider-array length, supplied by the explicit adapter

@dataclass(frozen=True)
class TokenMetadata:
    # None means not supplied. Empty string remains an explicitly supplied empty value.
    symbol: Optional[str] = None
    name: Optional[str] = None
    decimals: Optional[int] = None
    creator: Optional[str] = None
    created_at: Optional[str] = None       # source-reported token creation time
    first_seen_at: Optional[str] = None    # source-claimed first-seen time, not ground truth
    launchpad: Optional[str] = None
    website: Optional[str] = None
    social_links: Optional[Mapping[str, Any]] = None

@dataclass(frozen=True)
class DiscoveryObservation:
    context: DiscoveryContext
    metadata: TokenMetadata
    provider_record_id: Optional[str]
    raw_record: Mapping[str, Any]
    content_hash: str
    observation_key: str


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise A1ValidationError(f"{name} must be a non-empty string")
    return value


def validate_context(ctx: DiscoveryContext) -> None:
    _required_text(ctx.chain, "chain")
    mint = _required_text(ctx.mint, "mint")
    # Solana base58 addresses are case-sensitive. Validate alphabet/length without normalizing.
    if ctx.chain.lower() == "solana":
        if not (32 <= len(mint) <= 44) or not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]+", mint):
            raise A1ValidationError("invalid Solana base58 mint; identity is case-sensitive")
    for field in ("source_id", "endpoint_id", "run_id", "raw_response_id", "observed_at", "normalization_version"):
        _required_text(getattr(ctx, field), field)
    if isinstance(ctx.source_record_index, bool) or not isinstance(ctx.source_record_index, int) or ctx.source_record_index < 0:
        raise A1ValidationError("source_record_index must be a non-negative integer")
    if ctx.source_record_count is not None:
        if isinstance(ctx.source_record_count, bool) or not isinstance(ctx.source_record_count, int) or ctx.source_record_count < 1:
            raise A1ValidationError("source_record_count must be a positive integer")
        if ctx.source_record_index >= ctx.source_record_count:
            raise A1ValidationError("source_record_index is outside source_record_count")


def normalize_observation(context: DiscoveryContext, record: Mapping[str, Any], *,
                          field_map: Mapping[str, str], provider_record_id: Optional[str] = None) -> DiscoveryObservation:
    """Normalize only caller-declared field mappings; never guesses provider-specific keys."""
    validate_context(context)
    if not isinstance(record, Mapping):
        raise A1ValidationError("record must be a mapping")
    allowed = {"symbol", "name", "decimals", "creator", "created_at", "first_seen_at", "launchpad", "website", "social_links"}
    if set(field_map) - allowed:
        raise A1ValidationError("field_map contains unsupported canonical metadata fields")
    values = {}
    for canonical, provider_key in field_map.items():
        _required_text(provider_key, f"field_map[{canonical}]")
        if provider_key in record:
            values[canonical] = record[provider_key]
    if "decimals" in values and values["decimals"] is not None:
        if isinstance(values["decimals"], bool) or not isinstance(values["decimals"], int) or not 0 <= values["decimals"] <= 255:
            raise A1ValidationError("decimals must be an integer from 0 to 255")
    for k in ("symbol", "name", "creator", "created_at", "first_seen_at", "launchpad", "website"):
        if k in values and values[k] is not None and not isinstance(values[k], str):
            raise A1ValidationError(f"{k} must be a string or null")
    if "social_links" in values and values["social_links"] is not None and not isinstance(values["social_links"], Mapping):
        raise A1ValidationError("social_links must be an object or null")
    metadata = TokenMetadata(**values)
    canonical_record = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    content_hash = hashlib.sha256(canonical_record.encode()).hexdigest()
    key_material = {"chain": context.chain, "mint": context.mint, "source_id": context.source_id,
                    "endpoint_id": context.endpoint_id, "run_id": context.run_id,
                    "source_record_index": context.source_record_index, "content_hash": content_hash,
                    "normalization_version": context.normalization_version}
    observation_key = hashlib.sha256(json.dumps(key_material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return DiscoveryObservation(context, metadata, provider_record_id, dict(record), content_hash, observation_key)


def observation_to_dict(obs: DiscoveryObservation) -> dict:
    return {"context": asdict(obs.context), "metadata": asdict(obs.metadata),
            "provider_record_id": obs.provider_record_id, "raw_record": dict(obs.raw_record),
            "content_hash": obs.content_hash, "observation_key": obs.observation_key}
