from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from civitas.optimization import (
    ConstraintVerifier,
    Demand,
    InventoryLot,
    OptimizationEngine,
    OptimizationProblem,
    PlanningBucket,
    SupplierOffer,
    exhaustive_single_bucket_optimum,
)


@given(
    demand_quantity=st.integers(min_value=0, max_value=8),
    inventory_quantity=st.integers(min_value=0, max_value=5),
    first_capacity=st.integers(min_value=0, max_value=6),
    second_capacity=st.integers(min_value=0, max_value=6),
    first_cost=st.integers(min_value=0, max_value=5),
    second_cost=st.integers(min_value=0, max_value=5),
)
@settings(max_examples=40, deadline=None)
def test_small_generated_cases_match_exhaustive_enumeration(
    demand_quantity: int,
    inventory_quantity: int,
    first_capacity: int,
    second_capacity: int,
    first_cost: int,
    second_cost: int,
) -> None:
    start = datetime(2026, 8, 27, tzinfo=UTC)
    bucket = PlanningBucket("day-1", start, start + timedelta(days=1), urgency=2)
    problem = OptimizationProblem(
        planning_run_id="generated",
        buckets=(bucket,),
        demands=(Demand("demand", "sku", "warehouse", bucket.bucket_id, demand_quantity),),
        inventory_lots=(
            InventoryLot(
                "lot",
                "sku",
                "warehouse",
                inventory_quantity,
                bucket.end + timedelta(days=1),
            ),
        ),
        supplier_offers=(
            SupplierOffer(
                "offer-a",
                "supplier-a",
                "sku",
                "warehouse",
                bucket.bucket_id,
                first_capacity,
                first_cost,
            ),
            SupplierOffer(
                "offer-b",
                "supplier-b",
                "sku",
                "warehouse",
                bucket.bucket_id,
                second_capacity,
                second_cost,
            ),
        ),
    )

    result = OptimizationEngine().solve(problem)
    oracle = exhaustive_single_bucket_optimum(problem)

    assert result.optimal_weighted_shortage == oracle.weighted_shortage
    assert min(int(item.metrics["cost"]) for item in result.alternatives) == oracle.landed_cost
    for alternative in result.alternatives:
        assert ConstraintVerifier().verify(problem, alternative).valid
        assert sum(item.quantity for item in alternative.allocations) <= (
            inventory_quantity + sum(item.quantity for item in alternative.procurements)
        )
