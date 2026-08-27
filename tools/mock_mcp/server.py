"""Deterministic in-process procurement MCP simulation for offline tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime

from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult


@dataclass(slots=True)
class MockProcurementMCPServer:
    inventory: list[dict[str, object]] = field(default_factory=list)
    demand: list[dict[str, object]] = field(default_factory=list)
    supplier_offers: list[dict[str, object]] = field(default_factory=list)
    lead_times: list[dict[str, object]] = field(default_factory=list)
    warehouse_capacity: list[dict[str, object]] = field(default_factory=list)
    transport_capacity: list[dict[str, object]] = field(default_factory=list)
    observation_version: str = "mock-v1"
    _write_results: dict[str, MCPToolResult] = field(default_factory=dict, init=False)

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        if call.tool_name == "get_inventory":
            return self._read_result(call, "lots", self.inventory)
        if call.tool_name == "get_demand":
            return self._read_result(call, "demands", self.demand)
        if call.tool_name == "get_supplier_offers":
            return self._read_result(call, "offers", self.supplier_offers)
        if call.tool_name == "get_lead_times":
            return self._read_result(call, "records", self.lead_times)
        if call.tool_name == "get_warehouse_capacity":
            return self._read_result(call, "records", self.warehouse_capacity)
        if call.tool_name == "get_transport_capacity":
            return self._read_result(call, "records", self.transport_capacity)
        if call.tool_name == "reserve_inventory":
            return self._reserve_inventory(call)
        if call.tool_name == "create_procurement_order":
            return self._create_procurement_order(call)
        return MCPToolResult(
            call_id=call.call_id,
            succeeded=False,
            observed_at=datetime.now(UTC),
            payload={"tool_name": call.tool_name},
            error_code="unknown_tool",
            error_message=f"Unsupported tool {call.tool_name}.",
        )

    def _read_result(
        self,
        call: MCPToolCall,
        key: str,
        items: list[dict[str, object]],
    ) -> MCPToolResult:
        return MCPToolResult(
            call_id=call.call_id,
            succeeded=True,
            observed_at=datetime.now(UTC),
            payload={
                key: deepcopy(items),
                "observation_version": self.observation_version,
                "source_id": f"mock:{call.tool_name}",
            },
        )

    def _reserve_inventory(self, call: MCPToolCall) -> MCPToolResult:
        if call.access_mode is not MCPAccessMode.WRITE:
            return self._invalid_access(call)
        if call.idempotency_key is None:
            return self._missing_idempotency(call)
        existing = self._write_results.get(call.idempotency_key)
        if existing is not None:
            return existing
        lot_id = call.arguments.get("lot_id")
        quantity = call.arguments.get("quantity")
        reservation_id = f"reservation:{call.idempotency_key}"
        payload = {
            "reservation_id": reservation_id,
            "lot_id": lot_id,
            "quantity": quantity,
            "status": "reserved",
            "observation_version": self.observation_version,
            "source_id": "mock:reserve_inventory",
            "reservations": [
                {"reservation_id": reservation_id, "lot_id": lot_id, "quantity": quantity}
            ],
        }
        result = MCPToolResult(
            call_id=call.call_id,
            succeeded=True,
            observed_at=datetime.now(UTC),
            payload=payload,
        )
        self._write_results[call.idempotency_key] = result
        return result

    def _create_procurement_order(self, call: MCPToolCall) -> MCPToolResult:
        if call.access_mode is not MCPAccessMode.WRITE:
            return self._invalid_access(call)
        if call.idempotency_key is None:
            return self._missing_idempotency(call)
        existing = self._write_results.get(call.idempotency_key)
        if existing is not None:
            return existing
        order_id = f"po:{call.idempotency_key}"
        lines = call.arguments.get("lines", [])
        payload = {
            "order_id": order_id,
            "status": "created",
            "lines": deepcopy(lines) if isinstance(lines, list) else [],
            "observation_version": self.observation_version,
            "source_id": "mock:create_procurement_order",
            "orders": [{"order_id": order_id}],
        }
        result = MCPToolResult(
            call_id=call.call_id,
            succeeded=True,
            observed_at=datetime.now(UTC),
            payload=payload,
        )
        self._write_results[call.idempotency_key] = result
        return result

    def _invalid_access(self, call: MCPToolCall) -> MCPToolResult:
        return MCPToolResult(
            call_id=call.call_id,
            succeeded=False,
            observed_at=datetime.now(UTC),
            payload={"tool_name": call.tool_name},
            error_code="invalid_access_mode",
            error_message=f"{call.tool_name} requires write access.",
        )

    def _missing_idempotency(self, call: MCPToolCall) -> MCPToolResult:
        return MCPToolResult(
            call_id=call.call_id,
            succeeded=False,
            observed_at=datetime.now(UTC),
            payload={"tool_name": call.tool_name},
            error_code="missing_idempotency_key",
            error_message=f"{call.tool_name} requires an idempotency key.",
        )
