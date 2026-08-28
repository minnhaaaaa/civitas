"""Provider-neutral contracts for outbound operational MCP connections."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from civitas.contracts.common import Contract, JsonObject
from civitas.contracts.evidence import EvidenceRecord
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult


class ProviderAccessContext(StrEnum):
    """Credential and policy partitions at the outbound trust boundary."""

    PLANNING = "planning"
    DISSENT = "dissent"
    EXECUTION = "execution"


class OperationalEvidenceKind(StrEnum):
    INVENTORY = "inventory"
    DEMAND = "demand"
    SUPPLIER_OFFER = "supplier_offer"
    LEAD_TIME = "lead_time"
    WAREHOUSE_CAPACITY = "warehouse_capacity"
    TRANSPORT_CAPACITY = "transport_capacity"


class ProviderToolCapability(Contract):
    name: str = Field(min_length=1, max_length=128)
    access_mode: MCPAccessMode
    idempotent: bool = False
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_idempotent_writes(self) -> ProviderToolCapability:
        if self.access_mode is MCPAccessMode.WRITE and not self.idempotent:
            raise ValueError("provider write capabilities must declare idempotency")
        return self


class ProviderCapabilityManifest(Contract):
    provider_id: str = Field(min_length=1, max_length=128)
    server_name: str = Field(min_length=1, max_length=128)
    protocol_version: str = Field(min_length=1, max_length=64)
    discovered_at: datetime
    tools: tuple[ProviderToolCapability, ...]
    canonical_source_groups: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_duplicate_tools(self) -> ProviderCapabilityManifest:
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("provider capability names must be unique")
        return self


class ProviderRegistration(Contract):
    """Safe configuration persisted by Civitas; contains references, never secrets."""

    provider_id: str = Field(min_length=1, max_length=128)
    server_name: str = Field(min_length=1, max_length=128)
    endpoint: str = Field(min_length=1, max_length=2048)
    credential_refs: dict[ProviderAccessContext, str]
    enabled: bool = True

    @model_validator(mode="after")
    def require_isolated_credentials(self) -> ProviderRegistration:
        required = set(ProviderAccessContext)
        if set(self.credential_refs) != required:
            raise ValueError("planning, dissent, and execution credential references are required")
        refs = tuple(self.credential_refs.values())
        if any(not item.strip() for item in refs):
            raise ValueError("credential references cannot be empty")
        if len(refs) != len(set(refs)):
            raise ValueError("provider access contexts require distinct credential references")
        return self


class OperationalObservation(Contract):
    """A machine-verifiable fact parsed from a provider payload."""

    kind: OperationalEvidenceKind
    subject: str = Field(min_length=1, max_length=255)
    predicate: str = Field(min_length=1, max_length=128)
    value: Decimal | str | bool
    unit: str = Field(min_length=1, max_length=32)
    valid_at: datetime
    scope: JsonObject = Field(default_factory=dict)


class ProviderEvidenceRead(Contract):
    call: MCPToolCall
    result: MCPToolResult
    evidence: EvidenceRecord
    observations: tuple[OperationalObservation, ...]


class ProviderOnboardingReport(Contract):
    provider_id: str
    accepted: bool
    manifest: ProviderCapabilityManifest | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
