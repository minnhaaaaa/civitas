from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from civitas.application.plan_identity import selected_plan_hash
from civitas.contracts.common import Quantity
from civitas.contracts.enums import FeasibilityStatus
from civitas.contracts.optimization import CandidatePlan, ProcurementLine
from civitas.contracts.tools import MCPAccessMode, MCPToolCall
from civitas.runtime.simulated_provider import SimulatedProcurementTransport


@pytest.mark.asyncio
async def test_demo_provider_discovers_six_reads_and_idempotent_write() -> None:
    provider = SimulatedProcurementTransport("sku-1", "warehouse-1", "supplier-1")
    manifest = await provider.discover_capabilities()
    reads = {tool.name for tool in manifest.tools if tool.access_mode is MCPAccessMode.READ}
    assert reads == {
        "get_inventory",
        "get_demand",
        "get_supplier_offers",
        "get_lead_times",
        "get_warehouse_capacity",
        "get_transport_capacity",
    }

    call = MCPToolCall(
        call_id="write-1",
        server_name="civitas-simulator",
        tool_name="create_procurement_order",
        access_mode=MCPAccessMode.WRITE,
        idempotency_key="po-1",
        arguments={"supplier_id": "supplier-1"},
    )
    first = await provider.invoke(call)
    duplicate = await provider.invoke(call.model_copy(update={"call_id": "write-2"}))
    assert first.succeeded and duplicate.succeeded
    assert first.payload["order_id"] == duplicate.payload["order_id"]


def test_approval_hash_is_stable_across_database_decimal_scale() -> None:
    def plan(quantity: Decimal, cost: Decimal) -> CandidatePlan:
        return CandidatePlan(
            plan_id="plan-1",
            planning_run_id="run-1",
            feasibility=FeasibilityStatus.FULLY_FEASIBLE,
            procurement=(
                ProcurementLine(
                    supplier_id="supplier-1",
                    sku_id="sku-1",
                    destination_warehouse_id="warehouse-1",
                    arrival_bucket_start=datetime(2026, 8, 29, tzinfo=UTC),
                    quantity=Quantity(value=quantity, unit="each"),
                    landed_cost=cost,
                ),
            ),
            distribution=(),
            shortage_base_units=0,
            metrics={"cost": cost},
            solver_version="solver-v1",
        )

    assert selected_plan_hash(plan(Decimal("10"), Decimal("50"))) == selected_plan_hash(
        plan(Decimal("10.00000000"), Decimal("50.00000000"))
    )


def test_codex_remote_template_uses_secret_environment_variable() -> None:
    config = Path("deploy/codex.config.toml.example").read_text(encoding="utf-8")
    assert 'url = "http://127.0.0.1:8001/mcp"' in config
    assert 'bearer_token_env_var = "CIVITAS_CODEX_BEARER_TOKEN"' in config
    assert "demo-bearer-token" not in config
