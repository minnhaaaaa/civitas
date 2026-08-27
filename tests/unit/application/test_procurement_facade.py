from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from civitas.application.procurement_facade import (
    ProcurementApplicationFacade,
    WorkflowRunSnapshot,
)
from civitas.contracts.common import Quantity
from civitas.contracts.enums import FeasibilityStatus, JuryState
from civitas.contracts.jury import IntegrityComponents, JuryEvaluation, JuryGateResult
from civitas.contracts.mcp_product import (
    ApprovalChallenge,
    ApprovalReceipt,
    ApprovedTotals,
    GetDecisionSummaryRequest,
    GetPlanningRunRequest,
    PlanningProgress,
    PlanProcurementGoalRequest,
    PrepareExecutionRequest,
    ProcurementGoal,
)
from civitas.contracts.optimization import CandidatePlan, OptimizationResult, ProcurementLine
from civitas.ports.identity import OperatorContext
from civitas.workflow.models import ParliamentSession, WorkflowCheckpoint, WorkflowPhase

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


class IDs:
    def new_id(self, namespace: str) -> str:
        return f"{namespace}-1"


class Clock:
    def now(self) -> datetime:
        return NOW


class Runs:
    def __init__(self, snapshot: WorkflowRunSnapshot) -> None:
        self.snapshot = snapshot
        self.started = False

    async def start(self, **kwargs: object) -> WorkflowRunSnapshot:
        self.started = True
        assert kwargs["run_id"] == "run-1"
        return self.snapshot

    async def get(self, **kwargs: object) -> WorkflowRunSnapshot | None:
        return self.snapshot if kwargs["run_id"] == self.snapshot.run_id else None


class Approvals:
    async def prepare(self, **kwargs: object) -> ApprovalChallenge:
        summary = kwargs["summary"]
        assert summary.selected_plan_hash
        return ApprovalChallenge(
            challenge_id="challenge-1",
            challenge_secret="test-secret-is-long-enough",
            organization_id="org-1",
            operator_id="operator-1",
            run_id="run-1",
            selected_plan_hash=summary.selected_plan_hash,
            policy_version="decision-integrity-v1",
            approved_totals=ApprovedTotals(
                currency="USD",
                maximum_landed_cost="100",
                maximum_procurement_lines=1,
                maximum_distribution_lines=0,
            ),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

    async def approve(self, **kwargs: object) -> ApprovalReceipt:
        return ApprovalReceipt(
            receipt_id="approval-1",
            organization_id="org-1",
            operator_id="operator-1",
            run_id="run-1",
            selected_plan_hash="a" * 64,
            policy_version="decision-integrity-v1",
            approved_totals=ApprovedTotals(
                currency="USD",
                maximum_landed_cost="100",
                maximum_procurement_lines=1,
                maximum_distribution_lines=0,
            ),
            approved_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )


class Executions:
    async def execute(self, **kwargs: object):  # pragma: no cover - port wiring only
        raise AssertionError("not used")

    async def audit(self, **kwargs: object):  # pragma: no cover - port wiring only
        raise AssertionError("not used")


def _context() -> OperatorContext:
    return OperatorContext(
        organization_id="org-1",
        operator_id="operator-1",
        authentication_subject="subject",
        authenticated_at=NOW,
    )


def _goal() -> ProcurementGoal:
    return ProcurementGoal(
        objective="Cover demand",
        horizon_starts_at=NOW,
        horizon_ends_at=NOW + timedelta(days=1),
        timezone="UTC",
        sku_ids=("sku-1",),
        warehouse_ids=("warehouse-1",),
        maximum_cycles=2,
        model_call_budget=2,
        tool_call_budget=3,
        deadline_at=NOW + timedelta(hours=1),
    )


def _snapshot() -> WorkflowRunSnapshot:
    plan = CandidatePlan(
        plan_id="plan-1",
        planning_run_id="run-1",
        feasibility=FeasibilityStatus.FULLY_FEASIBLE,
        procurement=(
            ProcurementLine(
                supplier_id="supplier-1",
                sku_id="sku-1",
                destination_warehouse_id="warehouse-1",
                arrival_bucket_start=NOW,
                quantity=Quantity(value=Decimal("10"), unit="each"),
                landed_cost=Decimal("25"),
            ),
        ),
        shortage_base_units=0,
        metrics={"expected_waste_value": Decimal("2")},
        solver_version="solver-v1",
    )
    jury = JuryEvaluation(
        evaluation_id="jury-1",
        planning_run_id="run-1",
        plan_id="plan-1",
        policy_version="decision-integrity-v1",
        implementation_version="v1",
        calculated_at=NOW,
        components=IntegrityComponents(
            critical_claim_coverage=100,
            evidence_independence=100,
            provenance_completeness=100,
            evidence_freshness=100,
            canonical_source_diversity=100,
            contradiction_resolution=100,
            dissent_robustness=100,
        ),
        integrity_score=100,
        gates=(JuryGateResult(gate_code="all", passed=True),),
        state=JuryState.APPROVE,
        reason_codes=(),
    )
    checkpoint = WorkflowCheckpoint(
        planning_run_id="run-1",
        phase=WorkflowPhase.APPROVE,
        cycle=1,
        completed=True,
        final_state="approve",
        event_sequence=2,
        optimization_request={
            "planning_run_id": "run-1",
            "input_data_version": "v1",
            "objectives_version": "v1",
            "constraints": {},
        },
        optimization_result=OptimizationResult(planning_run_id="run-1", alternatives=(plan,)),
        parliament=ParliamentSession(selected_plan_id="plan-1"),
        jury_evaluation=jury,
    )
    return WorkflowRunSnapshot(
        organization_id="org-1",
        run_id="run-1",
        policy_version="decision-integrity-v1",
        created_at=NOW,
        updated_at=NOW,
        checkpoint=checkpoint,
        events=(
            PlanningProgress(
                sequence=1, occurred_at=NOW, phase="proposal", message="alternatives ready"
            ),
            PlanningProgress(sequence=2, occurred_at=NOW, phase="jury", message="approved"),
        ),
    )


@pytest.mark.asyncio
async def test_facade_starts_once_and_returns_deterministic_summary_and_progress() -> None:
    runs = Runs(_snapshot())
    facade = ProcurementApplicationFacade(
        workflow_runs=runs,
        approvals=Approvals(),
        executions=Executions(),
        ids=IDs(),
        clock=Clock(),
        audit_link_for=lambda run, cursor: f"/audit/{run}?cursor={cursor}",
    )

    started = await facade.plan_procurement_goal(
        _context(), PlanProcurementGoalRequest(goal=_goal())
    )
    first = await facade.get_planning_run(
        _context(), GetPlanningRunRequest(run_id="run-1", page_size=1)
    )
    second = await facade.get_planning_run(
        _context(), GetPlanningRunRequest(run_id="run-1", cursor=first.next_cursor, page_size=1)
    )
    summary = await facade.get_decision_summary(
        _context(), GetDecisionSummaryRequest(run_id="run-1")
    )

    assert runs.started and started.run.status.value == "ready_for_approval"
    assert [event.sequence for event in first.progress + second.progress] == [1, 2]
    assert summary.business_impact and summary.business_impact.total_landed_cost == Decimal("25")
    assert (
        summary.selected_plan_hash
        == (
            await facade.get_decision_summary(_context(), GetDecisionSummaryRequest(run_id="run-1"))
        ).selected_plan_hash
    )
    assert summary.audit_link == "/audit/run-1?cursor=2"


@pytest.mark.asyncio
async def test_prepare_execution_rejects_a_plan_hash_that_is_not_the_solver_selected_plan() -> None:
    facade = ProcurementApplicationFacade(
        workflow_runs=Runs(_snapshot()),
        approvals=Approvals(),
        executions=Executions(),
        ids=IDs(),
        clock=Clock(),
    )

    with pytest.raises(Exception, match="selected plan changed"):
        await facade.prepare_execution(
            _context(), PrepareExecutionRequest(run_id="run-1", selected_plan_hash="b" * 64)
        )
