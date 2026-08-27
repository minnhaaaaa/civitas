"""End-to-end product sequence through the MCP adapter and real facade.

The test deliberately uses deterministic in-memory ports.  It proves that the
transport cannot bypass the facade's plan-hash approval flow while keeping CI
independent of PostgreSQL, a model, and a procurement provider.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from civitas.application.procurement_facade import ProcurementApplicationFacade, WorkflowRunSnapshot
from civitas.contracts.common import Quantity
from civitas.contracts.enums import ExecutionState, FeasibilityStatus, JuryState
from civitas.contracts.jury import IntegrityComponents, JuryEvaluation, JuryGateResult
from civitas.contracts.mcp_product import (
    ApprovalChallenge,
    ApprovalReceipt,
    ApprovedTotals,
    ExecutionAuditEntry,
    ExecutionReceipt,
    PlanningProgress,
)
from civitas.contracts.optimization import CandidatePlan, OptimizationResult, ProcurementLine
from civitas.mcp_server import InboundMCPServer, StaticIdentityProvider
from civitas.ports.identity import OperatorContext
from civitas.workflow.models import ParliamentSession, WorkflowCheckpoint, WorkflowPhase

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)
PLAN_HASH = "a" * 64


class IDs:
    def new_id(self, namespace: str) -> str:
        return f"{namespace}-1"


class Clock:
    def now(self) -> datetime:
        return NOW


def _context() -> OperatorContext:
    return OperatorContext(
        organization_id="org-1",
        operator_id="operator-1",
        authentication_subject="test-subject",
        authenticated_at=NOW,
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
    return WorkflowRunSnapshot(
        organization_id="org-1",
        run_id="run-1",
        policy_version="decision-integrity-v1",
        created_at=NOW,
        updated_at=NOW,
        checkpoint=WorkflowCheckpoint(
            planning_run_id="run-1",
            phase=WorkflowPhase.APPROVE,
            cycle=1,
            completed=True,
            final_state="approve",
            event_sequence=1,
            optimization_request={
                "planning_run_id": "run-1",
                "input_data_version": "v1",
                "objectives_version": "v1",
                "constraints": {},
            },
            optimization_result=OptimizationResult(planning_run_id="run-1", alternatives=(plan,)),
            parliament=ParliamentSession(selected_plan_id="plan-1"),
            jury_evaluation=jury,
        ),
        events=(PlanningProgress(sequence=1, occurred_at=NOW, phase="jury", message="approved"),),
    )


class Runs:
    def __init__(self) -> None:
        self.snapshot = _snapshot()

    async def start(self, **_: object) -> WorkflowRunSnapshot:
        return self.snapshot

    async def get(self, **_: object) -> WorkflowRunSnapshot:
        return self.snapshot


class Approvals:
    def __init__(self) -> None:
        self.plan_hash: str | None = None

    async def prepare(self, **kwargs: object) -> ApprovalChallenge:
        summary = kwargs["summary"]
        self.plan_hash = summary.selected_plan_hash
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
                maximum_landed_cost="25",
                maximum_procurement_lines=1,
                maximum_distribution_lines=0,
            ),
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

    async def approve(self, **_: object) -> ApprovalReceipt:
        return ApprovalReceipt(
            receipt_id="approval-1",
            organization_id="org-1",
            operator_id="operator-1",
            run_id="run-1",
            selected_plan_hash=self.plan_hash or PLAN_HASH,
            policy_version="decision-integrity-v1",
            approved_totals=ApprovedTotals(
                currency="USD",
                maximum_landed_cost="25",
                maximum_procurement_lines=1,
                maximum_distribution_lines=0,
            ),
            approved_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )


class Executions:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *, context: OperatorContext, request: object) -> ExecutionReceipt:
        self.calls += 1
        return ExecutionReceipt(
            receipt_id="execution-1",
            organization_id=context.organization_id,
            run_id="run-1",
            selected_plan_hash=PLAN_HASH,
            idempotency_key=request.idempotency_key,
            execution_state=ExecutionState.SUCCEEDED,
            duplicate=self.calls > 1,
            executed_at=NOW,
            external_references=("order-1",),
        )

    async def audit(
        self, **kwargs: object
    ) -> tuple[ExecutionReceipt, tuple[ExecutionAuditEntry, ...]]:
        receipt = await self.execute(
            context=kwargs["context"], request=type("Request", (), {"idempotency_key": "idem-1"})()
        )
        return receipt, (
            ExecutionAuditEntry(sequence=1, occurred_at=NOW, state=ExecutionState.SUCCEEDED),
        )


@pytest.mark.asyncio
async def test_codex_mcp_sequence_uses_immutable_challenge_and_idempotent_receipt() -> None:
    executions = Executions()
    approvals = Approvals()
    facade = ProcurementApplicationFacade(
        workflow_runs=Runs(),
        approvals=approvals,
        executions=executions,
        ids=IDs(),
        clock=Clock(),
        audit_link_for=lambda run_id, cursor: f"/audit/{run_id}?cursor={cursor}",
    )
    server = InboundMCPServer(facade, StaticIdentityProvider(_context()))
    goal = {
        "objective": "Cover demand",
        "horizon_starts_at": NOW.isoformat(),
        "horizon_ends_at": (NOW + timedelta(days=1)).isoformat(),
        "timezone": "UTC",
        "sku_ids": ["sku-1"],
        "warehouse_ids": ["warehouse-1"],
        "maximum_cycles": 2,
        "model_call_budget": 0,
        "tool_call_budget": 3,
        "deadline_at": (NOW + timedelta(hours=1)).isoformat(),
    }

    planned = await server.dispatch("plan_procurement_goal", {"goal": goal})
    summary = await server.dispatch("get_decision_summary", {"run_id": planned["run"]["run_id"]})
    prepared = await server.dispatch(
        "prepare_execution",
        {"run_id": "run-1", "selected_plan_hash": summary["selected_plan_hash"]},
    )
    approved = await server.dispatch(
        "approve_execution",
        {
            "challenge_id": prepared["challenge"]["challenge_id"],
            "challenge_secret": prepared["challenge"]["challenge_secret"],
        },
    )
    request = {"receipt_id": approved["receipt"]["receipt_id"], "idempotency_key": "idem-1"}
    first = await server.dispatch("execute_approved_plan", request)
    retry = await server.dispatch("execute_approved_plan", request)
    audit = await server.dispatch(
        "get_execution_audit", {"run_id": "run-1", "execution_receipt_id": "execution-1"}
    )

    assert prepared["challenge"]["selected_plan_hash"] == summary["selected_plan_hash"]
    assert first["execution"]["duplicate"] is False
    assert retry["execution"]["duplicate"] is True
    assert audit["entries"][0]["state"] == "succeeded"
