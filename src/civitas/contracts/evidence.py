"""Evidence identity, provenance, and lineage contracts."""

from datetime import datetime

from pydantic import Field

from civitas.contracts.common import Contract, JsonObject
from civitas.contracts.enums import EvidenceOrigin


class EvidenceIdentity(Contract):
    canonical_source_id: str = Field(min_length=1)
    canonical_source_type: str = Field(min_length=1)
    mcp_server: str | None = None
    tool_name: str | None = None
    normalized_arguments: JsonObject = Field(default_factory=dict)
    retrieved_at: datetime
    observation_version: str | None = None
    raw_response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvidenceRecord(Contract):
    evidence_id: str = Field(min_length=1)
    claim_ids: tuple[str, ...] = ()
    identity: EvidenceIdentity
    origin: EvidenceOrigin
    agent_id: str | None = None
    content_summary: str = Field(min_length=1)
    derived_from: tuple[str, ...] = ()
    raw_payload: JsonObject | None = None
