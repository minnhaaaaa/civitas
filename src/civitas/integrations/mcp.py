"""Provider-neutral MCP clients, policies, and evidence conversion."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from civitas.contracts.enums import EvidenceOrigin
from civitas.contracts.evidence import EvidenceIdentity, EvidenceRecord
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.ports.mcp import MCPPort


class MCPIntegrationError(RuntimeError):
    """Base class for normalized MCP integration failures."""

    code: str

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class MCPAccessError(MCPIntegrationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="access_denied")


class MCPInvocationError(MCPIntegrationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="invocation_failed")


class MCPTransport(Protocol):
    async def invoke(self, call: MCPToolCall) -> MCPToolResult: ...


@dataclass(frozen=True, slots=True)
class MCPCapabilityPolicy:
    read_tools: frozenset[str]
    write_tools: frozenset[str]

    def allows(self, access_mode: MCPAccessMode, tool_name: str) -> bool:
        allowed = self.read_tools if access_mode is MCPAccessMode.READ else self.write_tools
        return tool_name in allowed


@dataclass(frozen=True, slots=True)
class CleanRoomNamespace:
    context_id: str
    memory_namespace: str
    tool_cache_namespace: str


@dataclass(frozen=True, slots=True)
class ToolEvidenceMapping:
    canonical_source_type: str
    canonical_source_id: str | None = None
    content_summary: str | None = None


class MCPClient(MCPPort):
    """Policy-enforced MCP client that preserves provider neutrality."""

    def __init__(self, *, transport: MCPTransport, policy: MCPCapabilityPolicy) -> None:
        self._transport = transport
        self._policy = policy

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        if not self._policy.allows(call.access_mode, call.tool_name):
            raise MCPAccessError(
                f"{call.access_mode.value} access denied for tool {call.tool_name}."
            )
        result = await self._transport.invoke(call)
        if not result.succeeded:
            detail = result.error_message or "MCP tool invocation failed."
            raise MCPInvocationError(f"{call.tool_name} failed: {detail}")
        return result


class DissentMCPClient(MCPClient):
    """Read-only MCP client with isolated namespaces for clean-room checks."""

    def __init__(self, *, transport: MCPTransport, namespace: CleanRoomNamespace) -> None:
        super().__init__(
            transport=transport,
            policy=MCPCapabilityPolicy(read_tools=_READ_TOOLS, write_tools=frozenset()),
        )
        self.namespace = namespace


class FakeMCPAdapter(MCPPort):
    """Deterministic MCP adapter for tests."""

    def __init__(self, results: Mapping[str, MCPToolResult]) -> None:
        self._results = dict(results)

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        result = self._results.get(call.call_id)
        if result is None:
            raise MCPInvocationError(f"No fake MCP result configured for {call.call_id}.")
        if not result.succeeded:
            raise MCPInvocationError(result.error_message or "Fake MCP call failed.")
        return result


def clean_room_namespace(label: str) -> CleanRoomNamespace:
    slug = _slugify(label)
    return CleanRoomNamespace(
        context_id=f"{slug}-context",
        memory_namespace=f"{slug}-memory",
        tool_cache_namespace=f"{slug}-tool-cache",
    )


def evidence_from_tool_result(
    *,
    evidence_id: str,
    call: MCPToolCall,
    result: MCPToolResult,
    mapping: ToolEvidenceMapping | None = None,
    claim_ids: Sequence[str] = (),
    agent_id: str | None = None,
    derived_from: Sequence[str] = (),
) -> EvidenceRecord:
    payload = result.payload
    canonical_source_type = mapping.canonical_source_type if mapping else call.server_name
    canonical_source_id = (
        mapping.canonical_source_id
        if mapping and mapping.canonical_source_id is not None
        else _resolve_canonical_source_id(call, payload)
    )
    summary = (
        mapping.content_summary
        if mapping and mapping.content_summary
        else _summarize_payload(call.tool_name, payload)
    )
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    normalized_arguments = cast(
        dict[str, object],
        json.loads(json.dumps(call.arguments, sort_keys=True)),
    )
    return EvidenceRecord(
        evidence_id=evidence_id,
        claim_ids=tuple(claim_ids),
        identity=EvidenceIdentity(
            canonical_source_id=canonical_source_id,
            canonical_source_type=canonical_source_type,
            mcp_server=call.server_name,
            tool_name=call.tool_name,
            normalized_arguments=normalized_arguments,
            retrieved_at=result.observed_at,
            observation_version=_extract_observation_version(payload),
            raw_response_sha256=fingerprint,
        ),
        origin=EvidenceOrigin.EXTERNAL,
        agent_id=agent_id,
        content_summary=summary,
        derived_from=tuple(derived_from),
        raw_payload=payload,
    )


def _extract_observation_version(payload: Mapping[str, object]) -> str | None:
    for key in ("observation_version", "version", "etag"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _resolve_canonical_source_id(call: MCPToolCall, payload: Mapping[str, object]) -> str:
    for key in ("canonical_source_id", "source_id", "dataset_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return f"{call.server_name}:{call.tool_name}"


def _summarize_payload(tool_name: str, payload: Mapping[str, object]) -> str:
    count = _payload_item_count(payload)
    if count is None:
        return f"{tool_name} returned structured MCP data."
    return f"{tool_name} returned {count} record(s)."


def _payload_item_count(payload: Mapping[str, object]) -> int | None:
    for key in ("items", "records", "lots", "offers", "demands", "reservations", "orders"):
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def _slugify(label: str) -> str:
    pieces = [character.lower() if character.isalnum() else "-" for character in label.strip()]
    collapsed = "".join(pieces).strip("-")
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    return collapsed or "clean-room"


_READ_TOOLS = frozenset(
    {
        "get_inventory",
        "get_demand",
        "get_supplier_offers",
        "get_lead_times",
        "get_warehouse_capacity",
        "get_transport_capacity",
    }
)


DEFAULT_PROCUREMENT_POLICY = MCPCapabilityPolicy(
    read_tools=_READ_TOOLS,
    write_tools=frozenset({"create_procurement_order", "reserve_inventory"}),
)
