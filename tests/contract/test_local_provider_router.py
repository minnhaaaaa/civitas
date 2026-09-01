"""Multi-provider routing and declarative mapping contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from civitas.contracts.provider_config import (
    CanonicalCapability,
    CapabilityBinding,
    CapabilityMapping,
    CollectionMapping,
    LocalProviderConfiguration,
    ProviderDefinition,
    ProviderMode,
    RequestMapping,
    StdioMCPTransport,
)
from civitas.contracts.providers import (
    ProviderAccessContext,
    ProviderCapabilityManifest,
    ProviderToolCapability,
)
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.integrations.local_router import LocalCapabilityRouter
from civitas.integrations.mcp import MCPAccessError

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


class RecordingTransport:
    def __init__(
        self,
        provider_id: str,
        tools: tuple[ProviderToolCapability, ...],
        payloads: dict[str, dict[str, object]],
    ) -> None:
        self.provider_id = provider_id
        self.tools = tools
        self.payloads = payloads
        self.calls: list[MCPToolCall] = []

    async def discover_capabilities(self) -> ProviderCapabilityManifest:
        return ProviderCapabilityManifest(
            provider_id=self.provider_id,
            server_name=self.provider_id,
            protocol_version="2025-06-18",
            discovered_at=NOW,
            tools=self.tools,
        )

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        self.calls.append(call)
        return MCPToolResult(
            call_id=call.call_id,
            succeeded=True,
            observed_at=NOW,
            payload=self.payloads[call.tool_name],
        )


def _provider(provider_id: str) -> ProviderDefinition:
    return ProviderDefinition(
        provider_id=provider_id,
        display_name=provider_id.title(),
        enabled=True,
        transport=StdioMCPTransport(command=f"{provider_id}-mcp"),
    )


def _read_tool(name: str) -> ProviderToolCapability:
    return ProviderToolCapability(name=name, access_mode=MCPAccessMode.READ)


def _write_tool(name: str) -> ProviderToolCapability:
    return ProviderToolCapability(
        name=name,
        access_mode=MCPAccessMode.WRITE,
        idempotent=True,
    )


@pytest.mark.asyncio
async def test_router_discovers_only_bound_canonical_tools_across_providers() -> None:
    warehouse = RecordingTransport(
        "warehouse",
        (_read_tool("get_stock"), _read_tool("internal_debug")),
        {"get_stock": {"lots": []}},
    )
    supplier = RecordingTransport(
        "supplier",
        (_read_tool("offers"),),
        {"offers": {"offers": []}},
    )
    configuration = LocalProviderConfiguration(
        mode=ProviderMode.LIVE,
        providers=(_provider("warehouse"), _provider("supplier")),
        bindings=(
            CapabilityBinding(
                canonical_capability=CanonicalCapability.GET_INVENTORY,
                provider_id="warehouse",
                tool_name="get_stock",
            ),
            CapabilityBinding(
                canonical_capability=CanonicalCapability.GET_SUPPLIER_OFFERS,
                provider_id="supplier",
                tool_name="offers",
            ),
        ),
    )
    router = LocalCapabilityRouter(
        configuration=configuration,
        context=ProviderAccessContext.PLANNING,
        transports={"warehouse": warehouse, "supplier": supplier},
    )

    manifest = await router.discover_capabilities()

    assert {tool.name for tool in manifest.tools} == {
        "get_inventory",
        "get_supplier_offers",
    }
    assert "internal_debug" not in {tool.name for tool in manifest.tools}


@pytest.mark.asyncio
async def test_router_maps_request_and_collection_response_without_code_execution() -> None:
    warehouse = RecordingTransport(
        "warehouse",
        (_read_tool("get_stock"),),
        {
            "get_stock": {
                "stock": [
                    {
                        "id": "lot-1",
                        "product": "sku-1",
                        "site": "warehouse-1",
                        "free": "12.5",
                        "uom": "kg",
                    }
                ]
            }
        },
    )
    configuration = LocalProviderConfiguration(
        providers=(_provider("warehouse"),),
        bindings=(
            CapabilityBinding(
                canonical_capability=CanonicalCapability.GET_INVENTORY,
                provider_id="warehouse",
                tool_name="get_stock",
                mapping_file="inventory.v1.json",
            ),
        ),
    )
    mapping = CapabilityMapping(
        request=RequestMapping(fields={"site": "/warehouse_id"}),
        response_collection=CollectionMapping(
            source_pointer="/stock",
            target_field="lots",
            fields={
                "lot_id": "/id",
                "sku_id": "/product",
                "warehouse_id": "/site",
                "available_quantity": "/free",
                "unit_of_measure": "/uom",
            },
        ),
    )
    router = LocalCapabilityRouter(
        configuration=configuration,
        context=ProviderAccessContext.PLANNING,
        transports={"warehouse": warehouse},
        mappings={"inventory.v1.json": mapping},
    )

    result = await router.invoke(
        MCPToolCall(
            call_id="call-inventory",
            server_name="local-router",
            tool_name="get_inventory",
            arguments={"organization_id": "org-1", "warehouse_id": "warehouse-1"},
            access_mode=MCPAccessMode.READ,
        )
    )

    assert warehouse.calls[0].tool_name == "get_stock"
    assert warehouse.calls[0].arguments == {"site": "warehouse-1"}
    assert result.payload == {
        "lots": [
            {
                "lot_id": "lot-1",
                "sku_id": "sku-1",
                "warehouse_id": "warehouse-1",
                "available_quantity": "12.5",
                "unit_of_measure": "kg",
            }
        ]
    }


@pytest.mark.asyncio
async def test_router_rejects_write_in_planning_context_without_provider_call() -> None:
    purchasing = RecordingTransport(
        "purchasing",
        (_write_tool("submit_po"),),
        {"submit_po": {"status": "created"}},
    )
    configuration = LocalProviderConfiguration(
        providers=(_provider("purchasing"),),
        bindings=(
            CapabilityBinding(
                canonical_capability=CanonicalCapability.CREATE_PROCUREMENT_ORDER,
                provider_id="purchasing",
                tool_name="submit_po",
            ),
        ),
    )
    router = LocalCapabilityRouter(
        configuration=configuration,
        context=ProviderAccessContext.PLANNING,
        transports={"purchasing": purchasing},
    )

    with pytest.raises(MCPAccessError, match="execution context"):
        await router.invoke(
            MCPToolCall(
                call_id="call-order",
                server_name="local-router",
                tool_name="create_procurement_order",
                arguments={"lines": []},
                access_mode=MCPAccessMode.WRITE,
                idempotency_key="execution-1:supplier-1",
            )
        )

    assert purchasing.calls == []


@pytest.mark.asyncio
async def test_execution_router_forwards_stable_idempotency_value_through_mapping() -> None:
    purchasing = RecordingTransport(
        "purchasing",
        (_write_tool("submit_po"),),
        {"submit_po": {"status": "created", "order_id": "po-1"}},
    )
    configuration = LocalProviderConfiguration(
        providers=(_provider("purchasing"),),
        bindings=(
            CapabilityBinding(
                canonical_capability=CanonicalCapability.CREATE_PROCUREMENT_ORDER,
                provider_id="purchasing",
                tool_name="submit_po",
                mapping_file="purchase.v1.json",
            ),
        ),
    )
    router = LocalCapabilityRouter(
        configuration=configuration,
        context=ProviderAccessContext.EXECUTION,
        transports={"purchasing": purchasing},
        mappings={
            "purchase.v1.json": CapabilityMapping(
                request=RequestMapping(
                    fields={
                        "items": "/lines",
                        "request_key": "/_civitas_idempotency_key",
                    }
                )
            )
        },
    )

    result = await router.invoke(
        MCPToolCall(
            call_id="call-order",
            server_name="local-router",
            tool_name="create_procurement_order",
            arguments={"lines": [{"sku_id": "sku-1", "quantity": "2"}]},
            access_mode=MCPAccessMode.WRITE,
            idempotency_key="execution-1:supplier-1",
        )
    )

    assert purchasing.calls[0].arguments == {
        "items": [{"sku_id": "sku-1", "quantity": "2"}],
        "request_key": "execution-1:supplier-1",
    }
    assert purchasing.calls[0].idempotency_key == "execution-1:supplier-1"
    assert result.payload["order_id"] == "po-1"
