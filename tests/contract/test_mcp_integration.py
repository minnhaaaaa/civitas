from __future__ import annotations

import pytest
from tools.mock_mcp import MockProcurementMCPServer

from civitas.contracts.tools import MCPAccessMode, MCPToolCall
from civitas.integrations import (
    DEFAULT_PROCUREMENT_POLICY,
    DissentMCPClient,
    MCPAccessError,
    MCPClient,
    ToolEvidenceMapping,
    clean_room_namespace,
    evidence_from_tool_result,
)


def _read_call(tool_name: str, *, call_id: str = "call-read") -> MCPToolCall:
    return MCPToolCall(
        call_id=call_id,
        server_name="mock-procurement",
        tool_name=tool_name,
        arguments={"sku_id": "SKU-1"},
        access_mode=MCPAccessMode.READ,
    )


def _write_call(
    tool_name: str,
    *,
    call_id: str = "call-write",
    idempotency_key: str = "idem-1",
) -> MCPToolCall:
    return MCPToolCall(
        call_id=call_id,
        server_name="mock-procurement",
        tool_name=tool_name,
        arguments={"lot_id": "LOT-1", "quantity": 5, "lines": [{"sku_id": "SKU-1", "quantity": 5}]},
        access_mode=MCPAccessMode.WRITE,
        idempotency_key=idempotency_key,
    )


@pytest.mark.asyncio
async def test_every_tool_result_can_be_converted_into_evidence_metadata() -> None:
    server = MockProcurementMCPServer(
        inventory=[{"lot_id": "LOT-1", "sku_id": "SKU-1", "available_quantity": 8}],
    )
    client = MCPClient(transport=server, policy=DEFAULT_PROCUREMENT_POLICY)
    call = _read_call("get_inventory")

    result = await client.invoke(call)
    evidence = evidence_from_tool_result(
        evidence_id="e-1",
        call=call,
        result=result,
        mapping=ToolEvidenceMapping(canonical_source_type="inventory_snapshot"),
        claim_ids=("claim-1",),
        agent_id="inventory_agent",
    )

    assert evidence.identity.mcp_server == "mock-procurement"
    assert evidence.identity.tool_name == "get_inventory"
    assert evidence.identity.normalized_arguments == {"sku_id": "SKU-1"}
    assert evidence.identity.observation_version == "mock-v1"
    assert evidence.content_summary == "get_inventory returned 1 record(s)."
    assert evidence.raw_payload == result.payload


@pytest.mark.asyncio
async def test_dissent_client_is_read_only() -> None:
    server = MockProcurementMCPServer()
    client = DissentMCPClient(transport=server, namespace=clean_room_namespace("dissent-1"))

    with pytest.raises(MCPAccessError, match="write access denied"):
        await client.invoke(_write_call("reserve_inventory"))


@pytest.mark.asyncio
async def test_repeated_write_idempotency_returns_original_result() -> None:
    server = MockProcurementMCPServer()
    client = MCPClient(transport=server, policy=DEFAULT_PROCUREMENT_POLICY)
    first = await client.invoke(
        _write_call("create_procurement_order", call_id="first", idempotency_key="same")
    )
    second = await client.invoke(
        _write_call("create_procurement_order", call_id="second", idempotency_key="same")
    )

    assert first.payload == second.payload
    assert first.observed_at == second.observed_at


def test_clean_room_namespaces_are_distinct_and_predictable() -> None:
    namespace = clean_room_namespace("Dissent Run 4")

    assert namespace.context_id == "dissent-run-4-context"
    assert namespace.memory_namespace == "dissent-run-4-memory"
    assert namespace.tool_cache_namespace == "dissent-run-4-tool-cache"


@pytest.mark.asyncio
async def test_required_contract_tests_stay_offline_with_mock_server() -> None:
    server = MockProcurementMCPServer(
        demand=[{"sku_id": "SKU-1", "warehouse_id": "WH-1", "quantity": 10}],
    )
    client = MCPClient(transport=server, policy=DEFAULT_PROCUREMENT_POLICY)

    result = await client.invoke(_read_call("get_demand"))

    assert result.succeeded is True
    assert result.payload["demands"] == [
        {"sku_id": "SKU-1", "warehouse_id": "WH-1", "quantity": 10}
    ]
