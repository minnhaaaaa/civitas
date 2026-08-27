"""Golden scenario execution against the deterministic optimizer and Jury."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from civitas.contracts import CandidatePlan, ExecutionRequest, ExecutionResult
from civitas.contracts.enums import ExecutionState, FeasibilityStatus, JuryState
from civitas.evidence import (
    EvidenceGraphProjector,
    JuryEvaluator,
    LineageAnalyzer,
    ReasonCode,
    detect_contradictions,
)
from civitas.optimization import (
    OptimizationEngine,
    select_minimax_regret,
    exhaustive_single_bucket_optimum,
)
from evaluation.scenarios import GoldenScenario


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    manifest: object
    actual_solver_status: FeasibilityStatus
    actual_jury_state: JuryState
    actual_reason_codes: frozenset[str]
    expected_reason_codes: frozenset[str]
    solver_status_match: bool
    jury_state_match: bool
    expected_lineage_pairs: frozenset[tuple[str, str]]
    actual_lineage_pairs: frozenset[tuple[str, str]]
    regret_checked: bool
    regret_match: bool
    oracle_checked: bool
    oracle_match: bool | None
    expected_execution_state: ExecutionState | None
    actual_execution_state: ExecutionState | None
    execution_state_match: bool | None


class ExecutionLedgerSimulator:
    """Minimal idempotency ledger for duplicate-execution scenarios."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def execute(self, request: ExecutionRequest, *, attempted_at: datetime) -> ExecutionResult:
        if request.idempotency_key in self._seen:
            return ExecutionResult(
                execution_id=request.execution_id,
                state=ExecutionState.DUPLICATE,
                attempted_at=attempted_at,
                completed_at=attempted_at,
                detail="duplicate idempotency key",
            )
        self._seen.add(request.idempotency_key)
        return ExecutionResult(
            execution_id=request.execution_id,
            state=ExecutionState.SUCCEEDED,
            attempted_at=attempted_at,
            completed_at=attempted_at,
            detail="simulated execution committed",
        )


def run_scenario(
    scenario: GoldenScenario,
    *,
    execution_ledger: ExecutionLedgerSimulator | None = None,
) -> ScenarioResult:
    solve_result = OptimizationEngine().solve(scenario.visible.problem)
    if scenario.visible.candidate_plan_override is None:
        if solve_result.alternatives:
            selection = select_minimax_regret(solve_result.alternatives)
            selected = next(
                item
                for item in solve_result.alternatives
                if item.alternative_id == selection.selected_alternative_id
            )
            candidate_plan = CandidatePlan(
                plan_id=selected.alternative_id,
                planning_run_id=scenario.visible.problem.planning_run_id,
                feasibility=selected.feasibility,
                shortage_base_units=selected.shortage,
                metrics=selected.metrics,
                solver_version="evaluation-harness",
            )
            maximum_regret = selection.maximum_regret[selected.alternative_id]
            total_regret = selection.total_regret[selected.alternative_id]
        else:
            candidate_plan = CandidatePlan(
                plan_id=f"{scenario.manifest.scenario_id}-infeasible",
                planning_run_id=scenario.visible.problem.planning_run_id,
                feasibility=FeasibilityStatus.INFEASIBLE,
                shortage_base_units=0,
                solver_version="evaluation-harness",
            )
            maximum_regret = None
            total_regret = None
    else:
        candidate_plan = scenario.visible.candidate_plan_override
        maximum_regret = None
        total_regret = None

    request = scenario.visible.jury_request.model_copy(update={"candidate_plan": candidate_plan})
    evaluation = JuryEvaluator().evaluate(
        request,
        scenario.visible.jury_inputs,
        evaluation_id=f"{scenario.manifest.scenario_id}-evaluation",
        calculated_at=scenario.manifest.calculated_at,
    )

    graph = EvidenceGraphProjector().project(
        scenario.visible.jury_inputs.claims, scenario.visible.jury_inputs.evidence
    )
    lineage = LineageAnalyzer(graph)
    actual_lineage_pairs = frozenset(
        (claim_id, group)
        for claim_id in scenario.expected_lineage.claim_source_groups
        for group in lineage.claim_source_groups(claim_id)
    )

    contradictions = detect_contradictions(
        scenario.visible.jury_inputs.claims,
        critical_claim_ids=scenario.visible.effective_critical_claim_ids,
    )
    actual_incomplete = frozenset(
        record.evidence_id
        for record in scenario.visible.jury_inputs.evidence
        if not lineage.has_complete_lineage(record.evidence_id)
    )
    expected_lineage_pairs = frozenset(
        (claim_id, group)
        for claim_id, groups in scenario.expected_lineage.claim_source_groups.items()
        for group in groups
    )
    assert frozenset(item.contradiction_id for item in contradictions) == scenario.expected_lineage.contradiction_ids
    assert actual_incomplete == scenario.expected_lineage.incomplete_lineage_evidence_ids

    oracle_checked = scenario.manifest.small_case_oracle
    oracle_match: bool | None = None
    if oracle_checked:
        oracle = exhaustive_single_bucket_optimum(scenario.visible.problem)
        oracle_match = (
            solve_result.optimal_weighted_shortage == oracle.weighted_shortage
            and (
                not solve_result.alternatives
                or min(int(item.metrics["cost"]) for item in solve_result.alternatives)
                == oracle.landed_cost
            )
        )

    regret_checked = scenario.expected_outcome.selected_maximum_regret_ceiling is not None
    regret_match = True
    if regret_checked:
        regret_match = (
            maximum_regret is not None
            and total_regret is not None
            and maximum_regret <= scenario.expected_outcome.selected_maximum_regret_ceiling
            and total_regret <= scenario.expected_outcome.selected_total_regret_ceiling
        )

    actual_execution_state: ExecutionState | None = None
    execution_state_match: bool | None = None
    if scenario.visible.execution_request is not None:
        ledger = execution_ledger or ExecutionLedgerSimulator()
        first = ledger.execute(scenario.visible.execution_request, attempted_at=scenario.manifest.calculated_at)
        execution_result = (
            ledger.execute(scenario.visible.execution_request, attempted_at=scenario.manifest.calculated_at)
            if scenario.visible.retry_execution
            else first
        )
        actual_execution_state = execution_result.state
        execution_state_match = actual_execution_state == scenario.expected_outcome.execution_state

    return ScenarioResult(
        manifest=scenario.manifest,
        actual_solver_status=solve_result.status,
        actual_jury_state=evaluation.state,
        actual_reason_codes=frozenset(evaluation.reason_codes),
        expected_reason_codes=scenario.expected_outcome.reason_codes,
        solver_status_match=solve_result.status == scenario.expected_outcome.solver_status,
        jury_state_match=evaluation.state == scenario.expected_outcome.jury_state,
        expected_lineage_pairs=expected_lineage_pairs,
        actual_lineage_pairs=actual_lineage_pairs,
        regret_checked=regret_checked,
        regret_match=regret_match,
        oracle_checked=oracle_checked,
        oracle_match=oracle_match,
        expected_execution_state=scenario.expected_outcome.execution_state,
        actual_execution_state=actual_execution_state,
        execution_state_match=execution_state_match,
    )


def run_suite(scenarios: tuple[GoldenScenario, ...]) -> tuple[ScenarioResult, ...]:
    ledger = ExecutionLedgerSimulator()
    return tuple(run_scenario(item, execution_ledger=ledger) for item in scenarios)
