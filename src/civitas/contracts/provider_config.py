"""Versioned contracts for local operational MCP provider routing."""

from __future__ import annotations

import ipaddress
import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, model_validator

from civitas.contracts.common import Contract, JsonObject
from civitas.contracts.providers import ProviderAccessContext

_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


class ProviderMode(StrEnum):
    LIVE = "live"
    SANDBOX = "sandbox"


class CanonicalCapability(StrEnum):
    GET_INVENTORY = "get_inventory"
    GET_DEMAND = "get_demand"
    GET_SUPPLIER_OFFERS = "get_supplier_offers"
    GET_LEAD_TIMES = "get_lead_times"
    GET_WAREHOUSE_CAPACITY = "get_warehouse_capacity"
    GET_TRANSPORT_CAPACITY = "get_transport_capacity"
    CREATE_PROCUREMENT_ORDER = "create_procurement_order"


class StdioMCPTransport(Contract):
    kind: Literal["stdio"] = "stdio"
    command: str = Field(min_length=1, max_length=1_024)
    args: tuple[str, ...] = Field(default=(), max_length=100)
    credential_env_refs: dict[ProviderAccessContext, dict[str, str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_process_configuration(self) -> StdioMCPTransport:
        if "\x00" in self.command or any("\x00" in argument for argument in self.args):
            raise ValueError("STDIO command and arguments cannot contain NUL bytes")
        for child_environment, local_reference in (
            item for references in self.credential_env_refs.values() for item in references.items()
        ):
            if not _ENVIRONMENT_NAME.fullmatch(child_environment):
                raise ValueError("child credential environment names must be uppercase identifiers")
            if not _ENVIRONMENT_NAME.fullmatch(local_reference):
                raise ValueError("credential references must be environment variable names")
        return self


class HttpMCPTransport(Contract):
    kind: Literal["streamable_http"] = "streamable_http"
    url: AnyHttpUrl
    authorization_env_refs: dict[ProviderAccessContext, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_secure_endpoint_and_secret_references(self) -> HttpMCPTransport:
        host = self.url.host
        is_loopback = host == "localhost"
        if host is not None and not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = False
        if self.url.scheme != "https" and not is_loopback:
            raise ValueError("provider endpoints require HTTPS or loopback HTTP")
        if self.url.username is not None or self.url.password is not None:
            raise ValueError("provider endpoint URLs cannot contain credentials")
        if self.url.query is not None:
            raise ValueError("provider endpoint URLs cannot contain query parameters")
        if self.url.fragment is not None:
            raise ValueError("provider endpoint URLs cannot contain fragments")
        if any(
            not _ENVIRONMENT_NAME.fullmatch(reference)
            for reference in self.authorization_env_refs.values()
        ):
            raise ValueError("authorization references must be environment variable names")
        return self


ProviderTransport = Annotated[
    StdioMCPTransport | HttpMCPTransport,
    Field(discriminator="kind"),
]


class ProviderDefinition(Contract):
    provider_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=100)
    transport: ProviderTransport
    enabled: bool = False


class CapabilityBinding(Contract):
    canonical_capability: CanonicalCapability
    provider_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    tool_name: str = Field(min_length=1, max_length=128)
    mapping_file: str | None = Field(default=None, min_length=1, max_length=1_024)
    enabled: bool = True


class RequestMapping(Contract):
    fields: dict[str, str] = Field(default_factory=dict, max_length=100)
    constants: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_pointers(self) -> RequestMapping:
        if any(not pointer.startswith("/") for pointer in self.fields.values()):
            raise ValueError("request field mappings must use absolute JSON Pointers")
        if set(self.fields) & set(self.constants):
            raise ValueError("request fields and constants cannot target the same argument")
        return self


class CollectionMapping(Contract):
    source_pointer: str = Field(min_length=1, max_length=1_024)
    target_field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    fields: dict[str, str] = Field(min_length=1, max_length=100)
    constants: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_pointers(self) -> CollectionMapping:
        pointers = (self.source_pointer, *self.fields.values())
        if any(not pointer.startswith("/") for pointer in pointers):
            raise ValueError("collection mappings must use absolute JSON Pointers")
        if set(self.fields) & set(self.constants):
            raise ValueError("collection fields and constants cannot target the same field")
        return self


class CapabilityMapping(Contract):
    mapping_version: Literal["1"] = "1"
    request: RequestMapping = Field(default_factory=RequestMapping)
    response_collection: CollectionMapping | None = None


class LocalProviderConfiguration(Contract):
    config_version: Literal["1"] = "1"
    mode: ProviderMode = ProviderMode.LIVE
    providers: tuple[ProviderDefinition, ...] = ()
    bindings: tuple[CapabilityBinding, ...] = ()

    @model_validator(mode="after")
    def validate_provider_graph(self) -> LocalProviderConfiguration:
        provider_ids = [provider.provider_id for provider in self.providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("provider identifiers must be unique")
        known_providers = set(provider_ids)
        unknown = sorted({binding.provider_id for binding in self.bindings} - known_providers)
        if unknown:
            raise ValueError("binding references unknown provider: " + ", ".join(unknown))
        active_capabilities = [
            binding.canonical_capability for binding in self.bindings if binding.enabled
        ]
        if len(active_capabilities) != len(set(active_capabilities)):
            raise ValueError("each capability may have only one active binding")
        return self
