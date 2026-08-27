"""Solver adapters and independent constraint verification."""

from civitas.optimization.adapter import OrToolsOptimizer, problem_from_request
from civitas.optimization.exhaustive import ExhaustiveOptimum, exhaustive_single_bucket_optimum
from civitas.optimization.models import (
    AllocationDecision,
    Alternative,
    Demand,
    InventoryLot,
    LotStatus,
    OptimizationProblem,
    PlanningBucket,
    ProcurementDecision,
    SolveResult,
    SupplierOffer,
    TransportLane,
    WarehouseCapacity,
)
from civitas.optimization.scorecards import (
    Role,
    SelectionResult,
    score_alternatives,
    select_minimax_regret,
)
from civitas.optimization.solver import SOLVER_VERSION, OptimizationEngine
from civitas.optimization.units import UnitConverter, UnitDefinition
from civitas.optimization.verifier import ConstraintVerifier, VerificationResult

__all__ = [
    "SOLVER_VERSION",
    "AllocationDecision",
    "Alternative",
    "ConstraintVerifier",
    "Demand",
    "ExhaustiveOptimum",
    "InventoryLot",
    "LotStatus",
    "OptimizationEngine",
    "OptimizationProblem",
    "OrToolsOptimizer",
    "PlanningBucket",
    "ProcurementDecision",
    "Role",
    "SelectionResult",
    "SolveResult",
    "SupplierOffer",
    "TransportLane",
    "UnitConverter",
    "UnitDefinition",
    "VerificationResult",
    "WarehouseCapacity",
    "exhaustive_single_bucket_optimum",
    "problem_from_request",
    "score_alternatives",
    "select_minimax_regret",
]
