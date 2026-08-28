from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tools.mock_mcp.server import MockProcurementMCPServer

from civitas.contracts import (
    CandidatePlan,
    ExecutionRequest,
    FeasibilityStatus,
    ProcurementLine,
    Quantity,
)
from civitas.execution import GuardedExecutionService, RevalidationSnapshot
from civitas.integrations import DEFAULT_EXECUTION_POLICY, ExecutionMCPClient
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator


class FixedClock(Clock):
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class CounterIDs(IDGenerator):
    def __init__(self) -> None:
        self._value = 0

    def new_id(self, namespace: str) -> str:
        self._value += 1
        return f"{namespace}-{self._value}"


def approved_plan() -> CandidatePlan:
    return CandidatePlan(
        plan_id="plan-1",
        planning_run_id="run-1",
        feasibility=FeasibilityStatus.FULLY_FEASIBLE,
        procurement=(
            ProcurementLine(
                supplier_id="supplier-b",
                sku_id="sku-apples",
                destination_warehouse_id="warehouse-north",
                arrival_bucket_start=datetime(2026, 8, 27, tzinfo=UTC),
                quantity=Quantity(value=Decimal("4"), unit="each"),
                landed_cost=Decimal("28"),
            ),
        ),
        shortage_base_units=0,
        metrics={},
        solver_version="solver-1",
    )


def execution_request(now: datetime) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id="execution-1",
        planning_run_id="run-1",
        approved_plan_id="plan-1",
        jury_evaluation_id="jury-1",
        idempotency_key="run-1:plan-1",
        approval_policy_version="decision-integrity-v1",
        requested_at=now,
        action={"kind": "procure"},
    )


@pytest.mark.asyncio
async def test_guarded_execution_succeeds_then_marks_duplicate() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    server = MockProcurementMCPServer(
        inventory=[{"lot_id": "lot-1"}],
        supplier_offers=[{"offer_id": "offer-b-live"}],
        lead_times=[{"supplier_id": "supplier-b", "lead_time_days": 1}],
        warehouse_capacity=[{"warehouse_id": "warehouse-north", "remaining_capacity_units": 20}],
    )
    service = GuardedExecutionService(
        mcp=ExecutionMCPClient(transport=server, policy=DEFAULT_EXECUTION_POLICY),
        ids=CounterIDs(),
        clock=FixedClock(now),
        server_name="mock-procurement",
    )
    snapshot = RevalidationSnapshot(
        inventory_lot_ids=frozenset({"lot-1"}),
        offer_ids=frozenset({"offer-b-live"}),
        lead_time_days={"supplier-b": 1},
        warehouse_capacity_units={"warehouse-north": 20},
    )

    first = await service.execute(
        execution_request(now),
        approved_plan=approved_plan(),
        expected_snapshot=snapshot,
    )
    second = await service.execute(
        execution_request(now),
        approved_plan=approved_plan(),
        expected_snapshot=snapshot,
    )

    assert first.state.value == "succeeded"
    assert second.state.value == "duplicate"
    assert first.external_references == second.external_references


@pytest.mark.asyncio
async def test_guarded_execution_fails_when_freshness_changes() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    server = MockProcurementMCPServer(
        inventory=[{"lot_id": "lot-1"}],
        supplier_offers=[{"offer_id": "offer-b-live"}],
        lead_times=[{"supplier_id": "supplier-b", "lead_time_days": 3}],
        warehouse_capacity=[{"warehouse_id": "warehouse-north", "remaining_capacity_units": 20}],
    )
    service = GuardedExecutionService(
        mcp=ExecutionMCPClient(transport=server, policy=DEFAULT_EXECUTION_POLICY),
        ids=CounterIDs(),
        clock=FixedClock(now),
        server_name="mock-procurement",
    )
    snapshot = RevalidationSnapshot(
        inventory_lot_ids=frozenset({"lot-1"}),
        offer_ids=frozenset({"offer-b-live"}),
        lead_time_days={"supplier-b": 1},
        warehouse_capacity_units={"warehouse-north": 20},
    )

    result = await service.execute(
        execution_request(now),
        approved_plan=approved_plan(),
        expected_snapshot=snapshot,
    )

    assert result.state.value == "failed"
    assert result.failure_code == "freshness_revalidation_failed"
