"""Composition of local multi-provider routing into the Civitas runtime."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from civitas.contracts.provider_config import (
    CanonicalCapability,
    CapabilityBinding,
    LocalProviderConfiguration,
    ProviderDefinition,
    StdioMCPTransport,
)
from civitas.contracts.providers import (
    ProviderAccessContext,
    ProviderCapabilityManifest,
    ProviderToolCapability,
)
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.integrations.providers import ExecutionProviderContext
from civitas.runtime.local_provider import build_local_provider_dependencies

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
READS = (
    CanonicalCapability.GET_INVENTORY,
    CanonicalCapability.GET_DEMAND,
    CanonicalCapability.GET_SUPPLIER_OFFERS,
    CanonicalCapability.GET_LEAD_TIMES,
    CanonicalCapability.GET_WAREHOUSE_CAPACITY,
    CanonicalCapability.GET_TRANSPORT_CAPACITY,
)


class FakeProviderTransport:
    def __init__(self, provider_id: str, tools: tuple[ProviderToolCapability, ...]) -> None:
        self.provider_id = provider_id
        self.tools = tools
        self.calls: list[MCPToolCall] = []

    async def discover_capabilities(self) -> ProviderCapabilityManifest:
        return ProviderCapabilityManifest(
            provider_id=self.provider_id,
            server_name=self.provider_id,
            protocol_version="test-v1",
            discovered_at=NOW,
            tools=self.tools,
        )

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        self.calls.append(call)
        payloads: dict[str, dict[str, object]] = {
            "get_inventory": {"lots": []},
            "get_demand": {"demands": []},
            "get_supplier_offers": {"offers": []},
            "get_lead_times": {"records": []},
            "get_warehouse_capacity": {"records": []},
            "get_transport_capacity": {"records": []},
            "create_procurement_order": {"status": "created", "order_id": "po-1"},
        }
        return MCPToolResult(
            call_id=call.call_id,
            succeeded=True,
            observed_at=NOW,
            payload=payloads[call.tool_name],
        )


def _configuration() -> LocalProviderConfiguration:
    operational = ProviderDefinition(
        provider_id="operations",
        display_name="Operations",
        enabled=True,
        transport=StdioMCPTransport(command="operations-mcp"),
    )
    purchasing = ProviderDefinition(
        provider_id="purchasing",
        display_name="Purchasing",
        enabled=True,
        transport=StdioMCPTransport(command="purchasing-mcp"),
    )
    return LocalProviderConfiguration(
        providers=(operational, purchasing),
        bindings=(
            *(
                CapabilityBinding(
                    canonical_capability=capability,
                    provider_id="operations",
                    tool_name=capability.value,
                )
                for capability in READS
            ),
            CapabilityBinding(
                canonical_capability=CanonicalCapability.CREATE_PROCUREMENT_ORDER,
                provider_id="purchasing",
                tool_name="create_procurement_order",
            ),
        ),
    )


@pytest.mark.asyncio
async def test_runtime_composes_reads_dissent_and_approval_bound_execution() -> None:
    created: dict[tuple[str, ProviderAccessContext], FakeProviderTransport] = {}

    def build_transport(
        provider: ProviderDefinition,
        context: ProviderAccessContext,
        write_tools: frozenset[str],
    ) -> FakeProviderTransport:
        tools = tuple(
            ProviderToolCapability(
                name=binding.tool_name,
                access_mode=(
                    MCPAccessMode.WRITE
                    if binding.tool_name in write_tools
                    else MCPAccessMode.READ
                ),
                idempotent=binding.tool_name in write_tools,
            )
            for binding in _configuration().bindings
            if binding.provider_id == provider.provider_id
        )
        transport = FakeProviderTransport(provider.provider_id, tools)
        created[(provider.provider_id, context)] = transport
        return transport

    dependencies = await build_local_provider_dependencies(
        configuration=_configuration(),
        mappings={},
        transport_builder=build_transport,
    )

    read = await dependencies.planning.evidence.read(
        call=MCPToolCall(
            call_id="read-1",
            server_name="local-provider-router",
            tool_name="get_inventory",
            arguments={"organization_id": "org-1"},
            access_mode=MCPAccessMode.READ,
        ),
        evidence_id="evidence-1",
    )
    assert read.result.payload == {"lots": []}
    assert dependencies.planning.dissent_namespace.context_id.startswith("local-provider")

    execution = await dependencies.execution.connections.connect(
        ExecutionProviderContext(
            execution_id="execution-1",
            approval_receipt_id="receipt-1",
            approved_plan_hash="a" * 64,
        )
    )
    result = await execution.invoke(
        MCPToolCall(
            call_id="write-1",
            server_name="local-provider-router",
            tool_name="create_procurement_order",
            arguments={"lines": []},
            access_mode=MCPAccessMode.WRITE,
            idempotency_key="execution-1:supplier-1",
        )
    )

    assert result.payload["order_id"] == "po-1"
    provider_call = created[("purchasing", ProviderAccessContext.EXECUTION)].calls[0]
    assert provider_call.arguments["_civitas_execution"] == {
        "execution_id": "execution-1",
        "approval_receipt_id": "receipt-1",
        "selected_plan_hash": "a" * 64,
    }
    assert provider_call.arguments["_civitas_idempotency_key"] == "execution-1:supplier-1"


@pytest.mark.asyncio
async def test_runtime_rejects_missing_required_read_capability() -> None:
    configuration = _configuration().model_copy(
        update={
            "bindings": tuple(
                binding
                for binding in _configuration().bindings
                if binding.canonical_capability is not CanonicalCapability.GET_DEMAND
            )
        }
    )

    with pytest.raises(ValueError, match="missing required capabilities: get_demand"):
        await build_local_provider_dependencies(
            configuration=configuration,
            mappings={},
            transport_builder=lambda provider, context, write_tools: FakeProviderTransport(
                provider.provider_id,
                (),
            ),
        )
