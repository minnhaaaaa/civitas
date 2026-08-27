from datetime import timedelta

from civitas.contracts.enums import FeasibilityStatus
from civitas.optimization import (
    ConstraintVerifier,
    Demand,
    InventoryLot,
    LotStatus,
    OptimizationEngine,
    OptimizationProblem,
    PlanningBucket,
    SupplierOffer,
    TransportLane,
    WarehouseCapacity,
    exhaustive_single_bucket_optimum,
)


def test_fully_feasible_result_matches_exhaustive_oracle(bucket: PlanningBucket) -> None:
    problem = OptimizationProblem(
        planning_run_id="run-1",
        buckets=(bucket,),
        demands=(Demand("demand", "apple", "w1", bucket.bucket_id, 6, priority=3),),
        supplier_offers=(
            SupplierOffer("cheap", "s1", "apple", "w1", bucket.bucket_id, 10, 2),
            SupplierOffer("expensive", "s2", "apple", "w1", bucket.bucket_id, 10, 5),
        ),
        maximum_alternatives=5,
    )

    result = OptimizationEngine().solve(problem)
    oracle = exhaustive_single_bucket_optimum(problem)

    assert result.status is FeasibilityStatus.FULLY_FEASIBLE
    assert result.optimal_weighted_shortage == oracle.weighted_shortage == 0
    assert min(int(item.metrics["cost"]) for item in result.alternatives) == oracle.landed_cost
    assert all(ConstraintVerifier().verify(problem, item).valid for item in result.alternatives)


def test_partial_and_infeasible_are_distinct(bucket: PlanningBucket) -> None:
    partial = OptimizationProblem(
        planning_run_id="partial",
        buckets=(bucket,),
        demands=(Demand("demand", "apple", "w1", bucket.bucket_id, 8),),
        inventory_lots=(InventoryLot("lot", "apple", "w1", 3, bucket.end + timedelta(days=1)),),
    )
    impossible = OptimizationProblem(
        planning_run_id="infeasible",
        buckets=(bucket,),
        demands=(
            Demand(
                "demand",
                "apple",
                "w1",
                bucket.bucket_id,
                8,
                minimum_service=4,
            ),
        ),
        inventory_lots=(InventoryLot("lot", "apple", "w1", 3, bucket.end + timedelta(days=1)),),
    )

    partial_result = OptimizationEngine().solve(partial)
    impossible_result = OptimizationEngine().solve(impossible)

    assert partial_result.status is FeasibilityStatus.PARTIALLY_FULFILLED
    assert partial_result.alternatives[0].shortage == 5
    assert impossible_result.status is FeasibilityStatus.INFEASIBLE
    assert impossible_result.alternatives == ()


def test_expired_and_quarantined_lots_are_never_allocated(bucket: PlanningBucket) -> None:
    problem = OptimizationProblem(
        planning_run_id="eligible-lots",
        buckets=(bucket,),
        demands=(Demand("demand", "milk", "w1", bucket.bucket_id, 5),),
        inventory_lots=(
            InventoryLot("expired", "milk", "w1", 10, bucket.start),
            InventoryLot(
                "quarantined",
                "milk",
                "w1",
                10,
                bucket.end + timedelta(days=2),
                status=LotStatus.QUARANTINED,
            ),
            InventoryLot("usable", "milk", "w1", 5, bucket.end + timedelta(days=1)),
        ),
    )

    alternative = OptimizationEngine().solve(problem).alternatives[0]

    assert {item.source_id for item in alternative.allocations} == {"usable"}
    assert ConstraintVerifier().verify(problem, alternative).valid


def test_fefo_consumes_earlier_expiry_first(bucket: PlanningBucket) -> None:
    problem = OptimizationProblem(
        planning_run_id="fefo",
        buckets=(bucket,),
        demands=(Demand("demand", "milk", "w1", bucket.bucket_id, 7),),
        inventory_lots=(
            InventoryLot("older", "milk", "w1", 5, bucket.end + timedelta(days=1)),
            InventoryLot("newer", "milk", "w1", 5, bucket.end + timedelta(days=3)),
        ),
    )

    alternative = OptimizationEngine().solve(problem).alternatives[0]
    allocated = {item.source_id: item.quantity for item in alternative.allocations}

    assert allocated == {"newer": 2, "older": 5}
    assert ConstraintVerifier().verify(problem, alternative).valid


def test_capacity_and_transport_are_conserved(bucket: PlanningBucket) -> None:
    second = PlanningBucket(
        bucket_id="day-2",
        start=bucket.end,
        end=bucket.end + timedelta(days=1),
        urgency=1,
    )
    problem = OptimizationProblem(
        planning_run_id="transport",
        buckets=(bucket, second),
        demands=(Demand("demand", "apple", "w2", second.bucket_id, 8),),
        inventory_lots=(InventoryLot("lot", "apple", "w1", 10, second.end + timedelta(days=1)),),
        warehouse_capacities=(WarehouseCapacity("w1", bucket.bucket_id, 10),),
        transport_lanes=(TransportLane("lane", "w1", "w2", ("apple",), 6, 1),),
    )

    result = OptimizationEngine().solve(problem)
    alternative = result.alternatives[0]

    assert result.status is FeasibilityStatus.PARTIALLY_FULFILLED
    assert alternative.shortage == 2
    assert sum(item.quantity for item in alternative.allocations) == 6
    assert ConstraintVerifier().verify(problem, alternative).valid


def test_solver_is_reproducible(bucket: PlanningBucket) -> None:
    problem = OptimizationProblem(
        planning_run_id="repeatable",
        buckets=(bucket,),
        demands=(Demand("demand", "apple", "w1", bucket.bucket_id, 4),),
        supplier_offers=(
            SupplierOffer("a", "s1", "apple", "w1", bucket.bucket_id, 8, 2),
            SupplierOffer("b", "s2", "apple", "w1", bucket.bucket_id, 8, 2),
        ),
    )

    first = OptimizationEngine().solve(problem)
    second = OptimizationEngine().solve(problem)

    assert first == second
