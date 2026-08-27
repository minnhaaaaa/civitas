from datetime import timedelta
from decimal import Decimal

from civitas.contracts.enums import FeasibilityStatus
from civitas.optimization import (
    AllocationDecision,
    Alternative,
    ConstraintVerifier,
    Demand,
    InventoryLot,
    OptimizationProblem,
    PlanningBucket,
)


def test_independent_verifier_detects_fefo_violation(bucket: PlanningBucket) -> None:
    problem = OptimizationProblem(
        planning_run_id="verify-fefo",
        buckets=(bucket,),
        demands=(Demand("demand", "milk", "w1", bucket.bucket_id, 2),),
        inventory_lots=(
            InventoryLot("older", "milk", "w1", 2, bucket.end + timedelta(days=1)),
            InventoryLot("newer", "milk", "w1", 2, bucket.end + timedelta(days=2)),
        ),
    )
    invalid = Alternative(
        alternative_id="invalid",
        feasibility=FeasibilityStatus.FULLY_FEASIBLE,
        weighted_shortage=0,
        shortage=0,
        procurements=(),
        allocations=(AllocationDecision("demand", "newer", "lot", 2),),
        metrics={"cost": Decimal(0)},
    )

    verification = ConstraintVerifier().verify(problem, invalid)

    assert not verification.valid
    assert any("FEFO violation" in item for item in verification.violations)


def test_verifier_detects_inventory_over_allocation(bucket: PlanningBucket) -> None:
    problem = OptimizationProblem(
        planning_run_id="verify-conservation",
        buckets=(bucket,),
        demands=(Demand("demand", "milk", "w1", bucket.bucket_id, 3),),
        inventory_lots=(InventoryLot("lot", "milk", "w1", 2, bucket.end + timedelta(days=1)),),
    )
    invalid = Alternative(
        alternative_id="invalid",
        feasibility=FeasibilityStatus.FULLY_FEASIBLE,
        weighted_shortage=0,
        shortage=0,
        procurements=(),
        allocations=(AllocationDecision("demand", "lot", "lot", 3),),
        metrics={},
    )

    verification = ConstraintVerifier().verify(problem, invalid)

    assert not verification.valid
    assert "lot lot is over-allocated" in verification.violations
