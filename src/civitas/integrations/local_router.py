"""Allowlisted routing across user-configured operational MCP servers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

from civitas.contracts.common import JsonObject, JsonValue
from civitas.contracts.provider_config import (
    CanonicalCapability,
    CapabilityBinding,
    CapabilityMapping,
    LocalProviderConfiguration,
    ProviderMode,
)
from civitas.contracts.providers import (
    ProviderAccessContext,
    ProviderCapabilityManifest,
    ProviderToolCapability,
)
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.integrations.mcp import MCPAccessError, MCPInvocationError
from civitas.ports.providers import OperationalProviderTransport

_WRITE_CAPABILITIES = frozenset({CanonicalCapability.CREATE_PROCUREMENT_ORDER})


class LocalCapabilityRouter:
    """Expose canonical Civitas capabilities over several provider MCPs."""

    def __init__(
        self,
        *,
        configuration: LocalProviderConfiguration,
        context: ProviderAccessContext,
        transports: Mapping[str, OperationalProviderTransport],
        mappings: Mapping[str, CapabilityMapping] | None = None,
    ) -> None:
        self._configuration = configuration
        self._context = context
        self._transports = dict(transports)
        self._mappings = dict(mappings or {})

    async def discover_capabilities(self) -> ProviderCapabilityManifest:
        bindings = self._context_bindings()
        provider_ids = tuple(dict.fromkeys(binding.provider_id for binding in bindings))
        manifests = await asyncio.gather(
            *(self._transport(provider_id).discover_capabilities() for provider_id in provider_ids)
        )
        by_provider = dict(zip(provider_ids, manifests, strict=True))
        canonical_tools: list[ProviderToolCapability] = []
        source_groups: dict[str, str] = {}
        for binding in bindings:
            manifest = by_provider[binding.provider_id]
            advertised = next(
                (tool for tool in manifest.tools if tool.name == binding.tool_name),
                None,
            )
            if advertised is None:
                raise MCPAccessError(
                    f"provider schema changed for capability {binding.canonical_capability.value}"
                )
            expected_mode = _access_mode(binding.canonical_capability)
            if advertised.access_mode is not expected_mode:
                raise MCPAccessError(
                    "provider access mode changed for capability "
                    f"{binding.canonical_capability.value}"
                )
            if expected_mode is MCPAccessMode.WRITE and not advertised.idempotent:
                raise MCPAccessError("purchase-order capability must advertise idempotency")
            canonical_tools.append(
                ProviderToolCapability(
                    name=binding.canonical_capability.value,
                    access_mode=expected_mode,
                    idempotent=advertised.idempotent,
                    description=advertised.description,
                )
            )
            source_groups[binding.canonical_capability.value] = (
                f"{binding.provider_id}:{binding.tool_name}"
            )
        return ProviderCapabilityManifest(
            provider_id="local-provider-router",
            server_name="local-provider-router",
            protocol_version="1",
            discovered_at=datetime.now(UTC),
            tools=tuple(canonical_tools),
            canonical_source_groups=source_groups,
        )

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        try:
            canonical = CanonicalCapability(call.tool_name)
        except ValueError as error:
            raise MCPAccessError("unknown canonical provider capability") from error
        expected_mode = _access_mode(canonical)
        if call.access_mode is not expected_mode:
            raise MCPAccessError("capability access mode does not match the canonical contract")
        if (
            expected_mode is MCPAccessMode.WRITE
            and self._context is not ProviderAccessContext.EXECUTION
        ):
            raise MCPAccessError("provider writes require the execution context")
        binding = self._binding(call.tool_name)
        source_arguments = dict(call.arguments)
        if call.idempotency_key is not None:
            source_arguments["_civitas_idempotency_key"] = call.idempotency_key
        mapping = self._mapping(binding)
        provider_arguments = (
            source_arguments if mapping is None else _map_request(source_arguments, mapping)
        )
        provider_call = call.model_copy(
            update={
                "server_name": binding.provider_id,
                "tool_name": binding.tool_name,
                "arguments": provider_arguments,
            }
        )
        result = await self._transport(binding.provider_id).invoke(provider_call)
        if not result.succeeded or mapping is None:
            return result.model_copy(update={"call_id": call.call_id})
        return result.model_copy(
            update={
                "call_id": call.call_id,
                "payload": _map_response(result.payload, mapping),
            }
        )

    def _context_bindings(self) -> tuple[CapabilityBinding, ...]:
        if self._configuration.mode is not ProviderMode.LIVE:
            raise MCPAccessError("live provider routing is disabled while sandbox mode is active")
        enabled_providers = {
            provider.provider_id for provider in self._configuration.providers if provider.enabled
        }
        return tuple(
            binding
            for binding in self._configuration.bindings
            if binding.enabled
            and binding.provider_id in enabled_providers
            and (
                self._context is ProviderAccessContext.EXECUTION
                or binding.canonical_capability not in _WRITE_CAPABILITIES
            )
        )

    def _binding(self, tool_name: str) -> CapabilityBinding:
        try:
            canonical = CanonicalCapability(tool_name)
        except ValueError as error:
            raise MCPAccessError("unknown canonical provider capability") from error
        binding = next(
            (item for item in self._context_bindings() if item.canonical_capability is canonical),
            None,
        )
        if binding is None:
            raise MCPAccessError(f"capability binding is unavailable: {tool_name}")
        return binding

    def _transport(self, provider_id: str) -> OperationalProviderTransport:
        try:
            return self._transports[provider_id]
        except KeyError as error:
            raise MCPAccessError("configured provider transport is unavailable") from error

    def _mapping(self, binding: CapabilityBinding) -> CapabilityMapping | None:
        if binding.mapping_file is None:
            return None
        try:
            return self._mappings[binding.mapping_file]
        except KeyError as error:
            raise MCPAccessError("configured capability mapping is unavailable") from error


def _access_mode(capability: CanonicalCapability) -> MCPAccessMode:
    return MCPAccessMode.WRITE if capability in _WRITE_CAPABILITIES else MCPAccessMode.READ


def _map_request(source: JsonObject, mapping: CapabilityMapping) -> JsonObject:
    mapped: JsonObject = dict(mapping.request.constants)
    for target, pointer in mapping.request.fields.items():
        mapped[target] = _json_pointer(source, pointer)
    return mapped


def _map_response(source: JsonObject, mapping: CapabilityMapping) -> JsonObject:
    collection_mapping = mapping.response_collection
    if collection_mapping is None:
        return source
    collection = _json_pointer(source, collection_mapping.source_pointer)
    if not isinstance(collection, list) or any(not isinstance(item, dict) for item in collection):
        raise MCPInvocationError("provider response collection is invalid")
    rows: list[JsonValue] = []
    for item in collection:
        assert isinstance(item, dict)
        row: JsonObject = dict(collection_mapping.constants)
        for target, pointer in collection_mapping.fields.items():
            row[target] = _json_pointer(item, pointer)
        rows.append(row)
    return {collection_mapping.target_field: rows}


def _json_pointer(source: JsonValue, pointer: str) -> JsonValue:
    current = source
    for raw_token in pointer.removeprefix("/").split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise MCPInvocationError("provider mapping source field is missing")
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as error:
                raise MCPInvocationError("provider mapping array index is invalid") from error
        else:
            raise MCPInvocationError("provider mapping traverses a scalar value")
    return current
