"""The single product facade used by MCP and guarded HTTP adapters.

This module intentionally orchestrates existing services rather than reimplementing
optimization, Jury, approval, or execution policy.  Its small ports make the
composition testable with deterministic in-memory fakes.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from civitas.application.plan_identity import selected_plan_hash
from civitas.contracts.mcp_product import (
    ApprovalChallenge,
    ApprovalReceipt,
    ApproveExecutionRequest,
    ApproveExecutionResponse,
    BusinessImpact,
    DecisionSummary,
    ExecuteApprovedPlanRequest,
    ExecuteApprovedPlanResponse,
    ExecutionAuditEntry,
    ExecutionAuditResponse,
    ExecutionReceipt,
    GetDecisionSummaryRequest,
    GetExecutionAuditRequest,
    GetPlanningRunRequest,
    IntegritySummary,
    PageRequest,
    PlanningProgress,
    PlanningRun,
    PlanningRunResponse,
    PlanningRunStatus,
    PlanProcurementGoalRequest,
    PrepareExecutionRequest,
    PrepareExecutionResponse,
    ProcurementGoal,
    ProductError,
    ProductErrorCode,
    ProductServiceError,
)
from civitas.contracts.optimization import CandidatePlan, OptimizationRequest
from civitas.ports.clock import Clock
from civitas.ports.identity import OperatorContext
from civitas.ports.ids import IDGenerator
from civitas.workflow.models import WorkflowCheckpoint, WorkflowLimits, WorkflowPhase


class WorkflowRunSnapshot:
    """State returned by the durable workflow boundary for one organization-scoped run."""

    def __init__(
        self,
        *,
        organization_id: str,
        run_id: str,
        policy_version: str,
        created_at: datetime,
        updated_at: datetime,
        checkpoint: WorkflowCheckpoint,
        events: tuple[PlanningProgress, ...] = (),
    ) -> None:
        self.organization_id = organization_id
        self.run_id = run_id
        self.policy_version = policy_version
        self.created_at = created_at
        self.updated_at = updated_at
        self.checkpoint = checkpoint
        self.events = events


class WorkflowRunStore(Protocol):
    """Durable planning/investigation boundary; implementations own resumability."""

    async def start(
        self,
        *,
        context: OperatorContext,
        run_id: str,
        goal: ProcurementGoal,
        optimization_request: OptimizationRequest,
        limits: WorkflowLimits,
    ) -> WorkflowRunSnapshot: ...

    async def get(self, *, context: OperatorContext, run_id: str) -> WorkflowRunSnapshot | None: ...


class ApprovalPort(Protocol):
    async def prepare(
        self, *, context: OperatorContext, summary: DecisionSummary
    ) -> ApprovalChallenge: ...

    async def approve(
        self, *, context: OperatorContext, request: ApproveExecutionRequest
    ) -> ApprovalReceipt: ...


class ApprovedExecutionPort(Protocol):
    async def execute(
        self, *, context: OperatorContext, request: ExecuteApprovedPlanRequest
    ) -> ExecutionReceipt: ...

    async def audit(
        self,
        *,
        context: OperatorContext,
        run_id: str,
        receipt_id: str,
        after_sequence: int,
        page_size: int,
    ) -> tuple[ExecutionReceipt, tuple[ExecutionAuditEntry, ...]]: ...


class AuditLinkIssuer(Protocol):
    async def issue(
        self,
        organization_id: str,
        run_id: str,
        selected_plan_id: str,
        maximum_event_sequence: int,
    ) -> str | None: ...


class ProcurementApplicationFacade:
    """Intent-level facade.  It is the only product-service implementation."""

    def __init__(
        self,
        *,
        workflow_runs: WorkflowRunStore,
        approvals: ApprovalPort,
        executions: ApprovedExecutionPort,
        ids: IDGenerator,
        clock: Clock,
        audit_links: AuditLinkIssuer | None = None,
        policy_version: str = "decision-integrity-v1",
    ) -> None:
        self._workflow_runs = workflow_runs
        self._approvals = approvals
        self._executions = executions
        self._ids = ids
        self._clock = clock
        self._audit_links = audit_links
        self._policy_version = policy_version

    async def plan_procurement_goal(
        self, context: OperatorContext, request: PlanProcurementGoalRequest
    ) -> PlanningRunResponse:
        run_id = self._ids.new_id("run")
        goal = request.goal
        snapshot = await self._workflow_runs.start(
            context=context,
            run_id=run_id,
            goal=goal,
            optimization_request=_optimization_request(run_id, goal),
            limits=_workflow_limits(goal),
        )
        return _run_response(snapshot, cursor=None, page_size=50)

    async def get_planning_run(
        self, context: OperatorContext, request: GetPlanningRunRequest
    ) -> PlanningRunResponse:
        snapshot = await self._require_run(context, request.run_id)
        return _run_response(snapshot, cursor=request.cursor, page_size=request.page_size)

    async def get_decision_summary(
        self, context: OperatorContext, request: GetDecisionSummaryRequest
    ) -> DecisionSummary:
        snapshot = await self._require_run(context, request.run_id)
        summary = _decision_summary(snapshot)
        if self._audit_links is None or summary.selected_plan_id is None:
            return summary
        link = await self._audit_links.issue(
            snapshot.organization_id,
            snapshot.run_id,
            summary.selected_plan_id,
            snapshot.checkpoint.event_sequence,
        )
        return summary.model_copy(update={"audit_link": link})

    async def prepare_execution(
        self, context: OperatorContext, request: PrepareExecutionRequest
    ) -> PrepareExecutionResponse:
        summary = await self.get_decision_summary(
            context, GetDecisionSummaryRequest(run_id=request.run_id)
        )
        if summary.selected_plan_hash != request.selected_plan_hash:
            raise _error(ProductErrorCode.CONFLICT, "selected plan changed; prepare a new approval")
        if summary.status is not PlanningRunStatus.READY_FOR_APPROVAL:
            raise _error(
                ProductErrorCode.INVESTIGATION_REQUIRED, "run is not eligible for execution"
            )
        return PrepareExecutionResponse(
            decision=summary,
            challenge=await self._approvals.prepare(context=context, summary=summary),
        )

    async def approve_execution(
        self, context: OperatorContext, request: ApproveExecutionRequest
    ) -> ApproveExecutionResponse:
        return ApproveExecutionResponse(
            receipt=await self._approvals.approve(context=context, request=request)
        )

    async def execute_approved_plan(
        self, context: OperatorContext, request: ExecuteApprovedPlanRequest
    ) -> ExecuteApprovedPlanResponse:
        return ExecuteApprovedPlanResponse(
            execution=await self._executions.execute(context=context, request=request)
        )

    async def get_execution_audit(
        self, context: OperatorContext, request: GetExecutionAuditRequest
    ) -> ExecutionAuditResponse:
        after = _decode_cursor(request)
        execution, entries = await self._executions.audit(
            context=context,
            run_id=request.run_id,
            receipt_id=request.execution_receipt_id,
            after_sequence=after,
            page_size=request.page_size + 1,
        )
        page = entries[: request.page_size]
        return ExecutionAuditResponse(
            execution=execution,
            entries=page,
            next_cursor=_encode_cursor(page[-1].sequence)
            if len(entries) > request.page_size and page
            else None,
        )

    async def _require_run(self, context: OperatorContext, run_id: str) -> WorkflowRunSnapshot:
        snapshot = await self._workflow_runs.get(context=context, run_id=run_id)
        if snapshot is None:
            raise _error(ProductErrorCode.NOT_FOUND, "planning run not found")
        # The store is responsible for organization-filtered lookup.  This explicit
        # check prevents a faulty adapter from turning a cross-org row into a leak.
        if snapshot.organization_id != context.organization_id:
            raise _error(ProductErrorCode.NOT_FOUND, "planning run not found")
        return snapshot


def _optimization_request(run_id: str, goal: ProcurementGoal) -> OptimizationRequest:
    constraints = dict(goal.constraints)
    input_data_version = str(constraints.pop("input_data_version", "pending-evidence"))
    objectives_version = str(constraints.pop("objectives_version", "feasibility-first-v1"))
    maximum_alternatives = constraints.get("maximum_alternatives", 5)
    if isinstance(maximum_alternatives, bool) or not isinstance(maximum_alternatives, int):
        maximum_alternatives = 5
    return OptimizationRequest(
        planning_run_id=run_id,
        input_data_version=input_data_version,
        objectives_version=objectives_version,
        constraints=constraints,
        maximum_alternatives=maximum_alternatives,
    )


def _workflow_limits(goal: ProcurementGoal) -> WorkflowLimits:
    return WorkflowLimits(
        max_cycles=goal.maximum_cycles,
        max_tool_calls=goal.tool_call_budget,
        deadline_at=goal.deadline_at,
    )


def _run_response(
    snapshot: WorkflowRunSnapshot, *, cursor: str | None, page_size: int
) -> PlanningRunResponse:
    after = _decode_cursor(PageRequest(cursor=cursor, page_size=page_size))
    events = tuple(event for event in snapshot.events if event.sequence > after)
    page = events[:page_size]
    selected = _selected_plan(snapshot.checkpoint)
    return PlanningRunResponse(
        run=PlanningRun(
            organization_id=snapshot.organization_id,
            run_id=snapshot.run_id,
            status=_run_status(snapshot.checkpoint),
            policy_version=snapshot.policy_version,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
            selected_plan_hash=selected_plan_hash(selected) if selected is not None else None,
            outstanding_investigation=snapshot.checkpoint.investigation_backlog,
        ),
        progress=page,
        next_cursor=_encode_cursor(page[-1].sequence) if len(events) > page_size and page else None,
    )


def _decision_summary(snapshot: WorkflowRunSnapshot) -> DecisionSummary:
    checkpoint = snapshot.checkpoint
    plan = _selected_plan(checkpoint)
    jury = checkpoint.jury_evaluation
    impact = _business_impact(plan) if plan else None
    return DecisionSummary(
        organization_id=snapshot.organization_id,
        run_id=snapshot.run_id,
        status=_run_status(checkpoint),
        policy_version=snapshot.policy_version,
        generated_at=snapshot.updated_at,
        selected_plan_id=plan.plan_id if plan else None,
        selected_plan_hash=selected_plan_hash(plan) if plan else None,
        business_impact=impact,
        integrity=(
            IntegritySummary(
                score=jury.integrity_score,
                state=jury.state,
                hard_gates_passed=all(gate.passed for gate in jury.gates),
                reason_codes=jury.reason_codes,
            )
            if jury
            else None
        ),
        material_uncertainties=checkpoint.investigation_backlog,
    )


def _selected_plan(checkpoint: WorkflowCheckpoint) -> CandidatePlan | None:
    if checkpoint.optimization_result is None or checkpoint.parliament is None:
        return None
    selected_id = checkpoint.parliament.selected_plan_id
    return next(
        (
            plan
            for plan in checkpoint.optimization_result.alternatives
            if plan.plan_id == selected_id
        ),
        None,
    )


def _run_status(checkpoint: WorkflowCheckpoint) -> PlanningRunStatus:
    phase = checkpoint.phase
    if phase is WorkflowPhase.APPROVE:
        return PlanningRunStatus.READY_FOR_APPROVAL
    if phase is WorkflowPhase.REJECT:
        return PlanningRunStatus.REJECTED
    if phase is WorkflowPhase.ESCALATE:
        return PlanningRunStatus.ESCALATED
    if phase is WorkflowPhase.INVESTIGATION:
        return PlanningRunStatus.INVESTIGATING
    return PlanningRunStatus.PLANNING


def _business_impact(plan: CandidatePlan) -> BusinessImpact:
    metrics = plan.metrics
    currency = str(metrics.get("currency", "USD"))
    return BusinessImpact(
        currency=currency,
        total_landed_cost=sum((line.landed_cost for line in plan.procurement), Decimal("0")),
        expected_waste_value=Decimal(str(metrics.get("expected_waste_value", "0"))),
        shortage_base_units=plan.shortage_base_units,
        procurement_line_count=len(plan.procurement),
        distribution_line_count=len(plan.distribution),
    )


def _decode_cursor(request: PageRequest) -> int:
    if request.cursor is None:
        return 0
    padded = request.cursor + "=" * (-len(request.cursor) % 4)
    return int(json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))["after"])


def _encode_cursor(after: int) -> str:
    encoded = json.dumps({"v": 1, "after": after}, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _error(code: ProductErrorCode, message: str) -> ProductServiceError:
    return ProductServiceError(
        ProductError(code=code, message=message, retryable=False, occurred_at=datetime.now(UTC))
    )
