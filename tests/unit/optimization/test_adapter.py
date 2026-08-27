from datetime import UTC, datetime, timedelta

import pytest

from civitas.contracts.optimization import OptimizationRequest
from civitas.optimization import OrToolsOptimizer, problem_from_request


def _request() -> OptimizationRequest:
    start = datetime(2026, 8, 27, tzinfo=UTC)
    return OptimizationRequest(
        planning_run_id="request-run",
        input_data_version="inputs-v1",
        objectives_version="objectives-v1",
        constraints={
            "base_unit": "g",
            "unit_definitions": {"kg": "1000", "g": "1"},
            "money_scale": 100,
            "buckets": [
                {
                    "bucket_id": "day-1",
                    "start": start.isoformat(),
                    "end": (start + timedelta(days=1)).isoformat(),
                    "urgency": 1,
                }
            ],
            "demands": [
                {
                    "demand_id": "demand",
                    "sku_id": "rice",
                    "warehouse_id": "w1",
                    "bucket_id": "day-1",
                    "quantity": {"value": "1.5", "unit": "kg"},
                }
            ],
            "supplier_offers": [
                {
                    "offer_id": "offer",
                    "supplier_id": "supplier",
                    "sku_id": "rice",
                    "destination_warehouse_id": "w1",
                    "arrival_bucket_id": "day-1",
                    "capacity": {"value": "2", "unit": "kg"},
                    "unit_cost": "0.01",
                    "pack_size": {"value": "0.5", "unit": "kg"},
                }
            ],
        },
        maximum_alternatives=3,
    )


def test_request_translation_converts_to_integer_base_units() -> None:
    problem, base_unit, money_scale = problem_from_request(_request())

    assert base_unit == "g"
    assert money_scale == 100
    assert problem.demands[0].quantity == 1500
    assert problem.supplier_offers[0].pack_size == 500


@pytest.mark.asyncio
async def test_port_adapter_returns_contract_plan() -> None:
    result = await OrToolsOptimizer().solve(_request())

    assert result.diagnostics["status"] == "fully_feasible"
    assert result.diagnostics["verified_alternatives"] == len(result.alternatives)
    assert result.alternatives
    assert result.alternatives[0].shortage_base_units == 0
    assert result.alternatives[0].procurement[0].quantity.unit == "g"
    assert result.alternatives[0].procurement[0].quantity.value == 1500
