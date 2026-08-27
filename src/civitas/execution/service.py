"""Guarded execution service with freshness revalidation and duplicate protection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from civitas.contracts.common import JsonObject
from civitas.contracts.enums import ExecutionState
from civitas.contracts.execution import ExecutionRequest, ExecutionResult
from civitas.contracts.optimization import CandidatePlan
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator
from civitas.ports.mcp import MCPPort


class FreshnessRevalidationError(RuntimeError):
    """Raised when refreshed operational facts no longer support the approved action."""


@dataclass(frozen=True, slots=True)
class RevalidationSnapshot:
    inventory_lot_ids: frozenset[str]
    offer_ids: frozenset[str]
    lead_time_days: Mapping[str, int]
    warehouse_capacity_units: Mapping[str, int]


class GuardedExecutionService:
    """Revalidates mutable inputs immediately before guarded MCP writes."""

    def __init__(
        self,
        *,
        mcp: MCPPort,
        ids: IDGenerator,
        clock: Clock,
        server_name: str,
    ) -> None:
        self._mcp = mcp
        self._ids = ids
        self._clock = clock
        self._server_name = server_name
        self._results_by_key: dict[str, ExecutionResult] = {}

    async def execute(
        self,
        request: ExecutionRequest,
        *,
        approved_plan: CandidatePlan,
        expected_snapshot: RevalidationSnapshot,
    ) -> ExecutionResult:
        existing = self._results_by_key.get(request.idempotency_key)
        attempted_at = self._clock.now()
        if existing is not None:
            duplicate = ExecutionResult(
                execution_id=request.execution_id,
                state=ExecutionState.DUPLICATE,
                attempted_at=attempted_at,
                completed_at=attempted_at,
                external_references=existing.external_references,
                detail="Duplicate idempotency key reused; prior execution preserved.",
            )
            self._results_by_key[request.idempotency_key] = duplicate
            return duplicate

        try:
            refreshed = await self._revalidate(request.planning_run_id, approved_plan)
            self._ensure_unchanged(expected_snapshot, refreshed, approved_plan)
            references = await self._commit_writes(request, approved_plan)
        except FreshnessRevalidationError as exc:
            failed = ExecutionResult(
                execution_id=request.execution_id,
                state=ExecutionState.FAILED,
                attempted_at=attempted_at,
                completed_at=self._clock.now(),
                failure_code="freshness_revalidation_failed",
                detail=str(exc),
            )
            self._results_by_key[request.idempotency_key] = failed
            return failed

        succeeded = ExecutionResult(
            execution_id=request.execution_id,
            state=ExecutionState.SUCCEEDED,
            attempted_at=attempted_at,
            completed_at=self._clock.now(),
            external_references=tuple(references),
            detail="Execution committed after freshness revalidation.",
        )
        self._results_by_key[request.idempotency_key] = succeeded
        return succeeded

    async def _revalidate(
        self, planning_run_id: str, approved_plan: CandidatePlan
    ) -> RevalidationSnapshot:
        inventory = await self._invoke_read(planning_run_id, "get_inventory", {})
        offers = await self._invoke_read(planning_run_id, "get_supplier_offers", {})
        lead_times = await self._invoke_read(planning_run_id, "get_lead_times", {})
        capacities = await self._invoke_read(planning_run_id, "get_warehouse_capacity", {})
        _ = approved_plan
        inventory_records = _records(inventory.payload, "lots")
        offer_records = _records(offers.payload, "offers")
        lead_time_records = _records(lead_times.payload, "records")
        capacity_records = _records(capacities.payload, "records")
        return RevalidationSnapshot(
            inventory_lot_ids=frozenset(
                str(item["lot_id"]) for item in inventory_records if "lot_id" in item
            ),
            offer_ids=frozenset(
                str(item["offer_id"]) for item in offer_records if "offer_id" in item
            ),
            lead_time_days={
                str(item["supplier_id"]): _required_int(item["lead_time_days"], "lead_time_days")
                for item in lead_time_records
                if "supplier_id" in item and "lead_time_days" in item
            },
            warehouse_capacity_units={
                str(item["warehouse_id"]): _required_int(
                    item["remaining_capacity_units"], "remaining_capacity_units"
                )
                for item in capacity_records
                if "warehouse_id" in item and "remaining_capacity_units" in item
            },
        )

    async def _invoke_read(
        self,
        planning_run_id: str,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> MCPToolResult:
        return await self._mcp.invoke(
            MCPToolCall(
                call_id=self._ids.new_id("mcp-read"),
                server_name=self._server_name,
                tool_name=tool_name,
                arguments=dict(arguments),
                access_mode=MCPAccessMode.READ,
                idempotency_key=f"{planning_run_id}:{tool_name}",
            )
        )

    def _ensure_unchanged(
        self,
        expected: RevalidationSnapshot,
        actual: RevalidationSnapshot,
        approved_plan: CandidatePlan,
    ) -> None:
        procurement_suppliers = {line.supplier_id for line in approved_plan.procurement}
        procurement_destinations = {
            line.destination_warehouse_id for line in approved_plan.procurement
        }
        if not expected.offer_ids <= actual.offer_ids:
            raise FreshnessRevalidationError(
                "Supplier offers changed since approval; execution requires investigation."
            )
        for supplier_id in procurement_suppliers:
            if expected.lead_time_days.get(supplier_id) != actual.lead_time_days.get(supplier_id):
                raise FreshnessRevalidationError(
                    f"Lead time changed for {supplier_id}; approved action is no longer current."
                )
        for warehouse_id in procurement_destinations:
            if expected.warehouse_capacity_units.get(
                warehouse_id
            ) != actual.warehouse_capacity_units.get(warehouse_id):
                raise FreshnessRevalidationError(
                    f"Warehouse capacity changed for {warehouse_id}; replan before execution."
                )
        for line in approved_plan.distribution:
            if not set(line.source_lot_ids) <= actual.inventory_lot_ids:
                raise FreshnessRevalidationError(
                    "A source lot referenced by the approved transfer is no longer available."
                )

    async def _commit_writes(
        self, request: ExecutionRequest, approved_plan: CandidatePlan
    ) -> Sequence[str]:
        if not approved_plan.procurement:
            return ()
        result = await self._mcp.invoke(
            MCPToolCall(
                call_id=self._ids.new_id("mcp-write"),
                server_name=self._server_name,
                tool_name="create_procurement_order",
                arguments={
                    "planning_run_id": request.planning_run_id,
                    "approved_plan_id": request.approved_plan_id,
                    "lines": [
                        {
                            "supplier_id": line.supplier_id,
                            "sku_id": line.sku_id,
                            "destination_warehouse_id": line.destination_warehouse_id,
                            "quantity": str(line.quantity.value),
                            "unit": line.quantity.unit,
                            "arrival_bucket_start": line.arrival_bucket_start.isoformat(),
                        }
                        for line in approved_plan.procurement
                    ],
                },
                access_mode=MCPAccessMode.WRITE,
                idempotency_key=request.idempotency_key,
            )
        )
        payload = result.payload
        order_id = payload.get("order_id")
        if isinstance(order_id, str):
            return (order_id,)
        return ()


def _records(payload: JsonObject, key: str) -> tuple[JsonObject, ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _required_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise FreshnessRevalidationError(f"MCP returned malformed {field}.")
    try:
        return int(value)
    except ValueError as error:
        raise FreshnessRevalidationError(f"MCP returned malformed {field}.") from error
