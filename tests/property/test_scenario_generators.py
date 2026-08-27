from evaluation import small_problem_strategy
from hypothesis import given, settings

from civitas.optimization import (
    ConstraintVerifier,
    OptimizationEngine,
    exhaustive_single_bucket_optimum,
)


@given(problem=small_problem_strategy())
@settings(max_examples=50, deadline=None)
def test_generated_small_cases_preserve_invariants_and_match_oracle(problem) -> None:  # type: ignore[no-untyped-def]
    result = OptimizationEngine().solve(problem)
    try:
        oracle = exhaustive_single_bucket_optimum(problem)
    except ValueError as exc:
        assert str(exc) == "problem has no feasible assignment"
        assert result.status.value == "infeasible"
        return

    assert result.optimal_weighted_shortage == oracle.weighted_shortage
    if result.alternatives:
        assert min(int(item.metrics["cost"]) for item in result.alternatives) == oracle.landed_cost
    for alternative in result.alternatives:
        assert ConstraintVerifier().verify(problem, alternative).valid


@given(problem=small_problem_strategy())
@settings(max_examples=25, deadline=None)
def test_generated_cases_remain_deterministic(problem) -> None:  # type: ignore[no-untyped-def]
    first = OptimizationEngine().solve(problem)
    second = OptimizationEngine().solve(problem)

    assert first.status == second.status
    assert first.optimal_weighted_shortage == second.optimal_weighted_shortage
    assert first.alternatives == second.alternatives
