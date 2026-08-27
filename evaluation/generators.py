"""Hypothesis generators for deterministic small evaluation cases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import strategies as st

from civitas.optimization import Demand, InventoryLot, OptimizationProblem, PlanningBucket, SupplierOffer


@st.composite
def small_problem_strategy(draw: st.DrawFn) -> OptimizationProblem:
    """Generate one-bucket problems that stay within exhaustive-oracle limits."""

    start = datetime(2026, 8, 27, tzinfo=UTC)
    bucket = PlanningBucket("day-1", start, start + timedelta(days=1), urgency=draw(st.integers(1, 3)))
    demand_quantity = draw(st.integers(min_value=0, max_value=8))
    inventory_quantity = draw(st.integers(min_value=0, max_value=6))
    offer_count = draw(st.integers(min_value=0, max_value=3))

    offers: list[SupplierOffer] = []
    for index in range(offer_count):
        capacity = draw(st.integers(min_value=0, max_value=6))
        pack_size = draw(st.sampled_from((1, 2)))
        minimum_order = draw(
            st.sampled_from(
                tuple(
                    value
                    for value in range(0, capacity + 1)
                    if value == 0 or (value % pack_size == 0 and value <= capacity)
                )
            )
        )
        offers.append(
            SupplierOffer(
                offer_id=f"offer-{index}",
                supplier_id=f"supplier-{index}",
                sku_id="sku-1",
                destination_warehouse_id="w-1",
                arrival_bucket_id=bucket.bucket_id,
                capacity=capacity,
                unit_cost=draw(st.integers(min_value=0, max_value=9)),
                pack_size=pack_size,
                minimum_order=minimum_order,
                risk=draw(st.integers(min_value=0, max_value=4)),
                expected_waste_rate=draw(st.integers(min_value=0, max_value=4)),
            )
        )

    return OptimizationProblem(
        planning_run_id="generated",
        buckets=(bucket,),
        demands=(
            Demand(
                demand_id="demand-1",
                sku_id="sku-1",
                warehouse_id="w-1",
                bucket_id=bucket.bucket_id,
                quantity=demand_quantity,
                priority=draw(st.integers(min_value=1, max_value=3)),
                minimum_service=draw(st.integers(min_value=0, max_value=demand_quantity)),
            ),
        ),
        inventory_lots=(
            InventoryLot(
                lot_id="lot-1",
                sku_id="sku-1",
                warehouse_id="w-1",
                quantity=inventory_quantity,
                expires_at=bucket.end + timedelta(days=draw(st.integers(min_value=1, max_value=3))),
                unit_cost=draw(st.integers(min_value=0, max_value=3)),
            ),
        ),
        supplier_offers=tuple(offers),
        maximum_alternatives=draw(st.integers(min_value=1, max_value=5)),
    )
