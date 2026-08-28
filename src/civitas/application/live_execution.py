"""Production adapters joining product approvals to guarded execution."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civitas.approval.service import (
    ApprovalError,
    ApprovalService,
    ChangedPlanError,
    ExpiredApprovalError,
)
from civitas.contracts.enums import ExecutionState
from civitas.contracts.execution import ExecutionRequest
from civitas.contracts.mcp_product import (
    ApprovalChallenge,
    ApprovalReceipt,
    ApprovedTotals,
    ApproveExecutionRequest,
    DecisionSummary,
    ExecuteApprovedPlanRequest,
    ExecutionAuditEntry,
    ExecutionReceipt,
    ProductError,
    ProductErrorCode,
    ProductServiceError,
)
from civitas.contracts.providers import ProviderRegistration
from civitas.execution.guarded import GuardedExecutionService
from civitas.integrations.mcp import clean_room_namespace
from civitas.integrations.providers import ExecutionProviderContext, ProviderOnboarder
from civitas.persistence.models import (
    ApprovalReceiptModel,
    CandidatePlanModel,
    ExecutionAuditEventModel,
    ExecutionAuditModel,
    JuryDecisionModel,
    PlanningRunModel,
)
from civitas.ports.clock import Clock
from civitas.ports.identity import OperatorContext
from civitas.ports.ids import IDGenerator
from civitas.ports.mcp import MCPPort


class ExecutionProviderConnectionFactory(Protocol):
    """Create one execution-only provider client for an approved action."""

    async def connect(self, context: ExecutionProviderContext) -> MCPPort: ...


class OnboardedExecutionConnectionFactory:
    """Connect Agent 3's isolated execution credential with immutable binding."""

    def __init__(
        self,
        *,
        onboarder: ProviderOnboarder,
        registration: ProviderRegistration,
    ) -> None:
        self._onboarder = onboarder
        self._registration = registration

    async def connect(self, context: ExecutionProviderContext) -> MCPPort:
        connections = await self._onboarder.connect(
            registration=self._registration,
            namespace=clean_room_namespace(f"execution-{context.execution_id}"),
            execution_context=context,
        )
        return connections.execution


class PersistedApprovalAdapter:
    """Product approval port backed by the immutable challenge ledger."""

    def __init__(self, service: ApprovalService, *, policy_version: str = "approval-v1") -> None:
        self._service = service
        self._policy_version = policy_version

    async def prepare(
        self, *, context: OperatorContext, summary: DecisionSummary
    ) -> ApprovalChallenge:
        if summary.selected_plan_hash is None or summary.business_impact is None:
            raise ValueError("selected plan and business impact are required for approval")
        impact = summary.business_impact
        return await self._service.issue(
            context=context,
            run_id=summary.run_id,
            selected_plan_hash=summary.selected_plan_hash,
            policy_version=self._policy_version,
            approved_totals=ApprovedTotals(
                currency=impact.currency,
                maximum_landed_cost=impact.total_landed_cost,
                maximum_procurement_lines=impact.procurement_line_count,
                maximum_distribution_lines=impact.distribution_line_count,
            ),
        )

    async def approve(
        self, *, context: OperatorContext, request: ApproveExecutionRequest
    ) -> ApprovalReceipt:
        try:
            return await self._service.approve(
                context=context,
                challenge_id=request.challenge_id,
                secret=request.challenge_secret,
            )
        except ExpiredApprovalError as error:
            raise _product_error(ProductErrorCode.EXPIRED_APPROVAL, str(error)) from error
        except ChangedPlanError as error:
            raise _product_error(ProductErrorCode.CONFLICT, str(error)) from error
        except ApprovalError as error:
            raise _product_error(ProductErrorCode.REJECTED_EXECUTION, str(error)) from error


class PersistedApprovedExecutionAdapter:
    """Product execution port that cannot bypass receipt or selected-plan binding."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        guarded: GuardedExecutionService,
        execution_connections: ExecutionProviderConnectionFactory,
        ids: IDGenerator,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._guarded = guarded
        self._execution_connections = execution_connections
        self._ids = ids
        self._clock = clock

    async def execute(
        self, *, context: OperatorContext, request: ExecuteApprovedPlanRequest
    ) -> ExecutionReceipt:
        async with self._sessions() as session:
            receipt = await session.scalar(
                select(ApprovalReceiptModel).where(
                    ApprovalReceiptModel.id == request.receipt_id,
                    ApprovalReceiptModel.organization_id == context.organization_id,
                    ApprovalReceiptModel.operator_id == context.operator_id,
                )
            )
            if receipt is None:
                raise ValueError("approval receipt not found")
            run = await session.scalar(
                select(PlanningRunModel).where(
                    PlanningRunModel.id == receipt.planning_run_id,
                    PlanningRunModel.organization_id == context.organization_id,
                )
            )
            if run is None:
                raise ValueError("planning run not found")
            plan = await session.scalar(
                select(CandidatePlanModel).where(
                    CandidatePlanModel.planning_run_id == run.id,
                    CandidatePlanModel.selected.is_(True),
                )
            )
            if plan is None:
                raise ValueError("selected plan not found")
            jury = await session.scalar(
                select(JuryDecisionModel)
                .where(
                    JuryDecisionModel.planning_run_id == run.id,
                    JuryDecisionModel.plan_id == plan.id,
                )
                .order_by(JuryDecisionModel.calculated_at.desc(), JuryDecisionModel.id.desc())
                .limit(1)
            )
            if jury is None:
                raise ValueError("jury evaluation not found")

            action = {
                "kind": "execute_selected_procurement_plan",
                "approval_receipt_id": receipt.id,
                "selected_plan_hash": receipt.selected_plan_hash,
            }
            duplicate = await session.scalar(
                select(ExecutionAuditModel).where(
                    ExecutionAuditModel.organization_id == context.organization_id,
                    ExecutionAuditModel.idempotency_key == request.idempotency_key,
                )
            )
            if duplicate is not None:
                if (
                    duplicate.planning_run_id != run.id
                    or duplicate.approved_plan_id != plan.id
                    or duplicate.jury_decision_id != jury.id
                    or duplicate.approval_policy_version != receipt.policy_version
                    or duplicate.action != action
                    or duplicate.approval_receipt_id != receipt.id
                ):
                    raise ValueError(
                        "idempotency key was reused for a different execution request"
                    )
                return _execution_receipt_from_audit(
                    duplicate, receipt.selected_plan_hash
                ).model_copy(update={"duplicate": True})

        execution_request = ExecutionRequest(
            execution_id=self._ids.new_id("execution"),
            planning_run_id=run.id,
            approved_plan_id=plan.id,
            jury_evaluation_id=jury.id,
            idempotency_key=request.idempotency_key,
            approval_policy_version=receipt.policy_version,
            requested_at=self._clock.now(),
            action=action,
        )
        execution_mcp = await self._execution_connections.connect(
            ExecutionProviderContext(
                execution_id=execution_request.execution_id,
                approval_receipt_id=receipt.id,
                approved_plan_hash=receipt.selected_plan_hash,
            )
        )
        outcome = await self._guarded.execute(
            execution_request,
            context=context,
            approval_receipt_id=receipt.id,
            write_mcp=execution_mcp,
        )
        return _execution_receipt(
            outcome.result,
            organization_id=context.organization_id,
            run_id=run.id,
            selected_hash=receipt.selected_plan_hash,
            idempotency_key=request.idempotency_key,
        )

    async def audit(
        self,
        *,
        context: OperatorContext,
        run_id: str,
        receipt_id: str,
        after_sequence: int,
        page_size: int,
    ) -> tuple[ExecutionReceipt, tuple[ExecutionAuditEntry, ...]]:
        async with self._sessions() as session:
            audit = await session.scalar(
                select(ExecutionAuditModel).where(
                    ExecutionAuditModel.id == receipt_id,
                    ExecutionAuditModel.organization_id == context.organization_id,
                    ExecutionAuditModel.planning_run_id == run_id,
                )
            )
            if audit is None:
                raise ValueError("execution receipt not found")
            approval = await session.scalar(
                select(ApprovalReceiptModel).where(
                    ApprovalReceiptModel.id == audit.approval_receipt_id,
                    ApprovalReceiptModel.organization_id == context.organization_id,
                )
            )
            if approval is None:
                raise ValueError("bound approval receipt not found")
            rows = (
                await session.scalars(
                    select(ExecutionAuditEventModel)
                    .where(
                        ExecutionAuditEventModel.execution_id == audit.id,
                        ExecutionAuditEventModel.sequence > after_sequence,
                    )
                    .order_by(ExecutionAuditEventModel.sequence)
                    .limit(page_size)
                )
            ).all()
        receipt = _execution_receipt_from_audit(audit, approval.selected_plan_hash)
        entries = tuple(
            ExecutionAuditEntry(
                sequence=row.sequence,
                occurred_at=row.occurred_at,
                state=ExecutionState(row.state),
                reason_code=row.reason_code,
                detail=row.detail,
            )
            for row in rows
        )
        return receipt, entries


def _execution_receipt(
    result: object,
    *,
    organization_id: str,
    run_id: str,
    selected_hash: str,
    idempotency_key: str,
) -> ExecutionReceipt:
    from civitas.contracts.execution import ExecutionResult

    assert isinstance(result, ExecutionResult)
    return ExecutionReceipt(
        receipt_id=result.execution_id,
        organization_id=organization_id,
        run_id=run_id,
        selected_plan_hash=selected_hash,
        idempotency_key=idempotency_key,
        execution_state=result.state,
        duplicate=result.state is ExecutionState.DUPLICATE,
        executed_at=result.completed_at or result.attempted_at,
        external_references=result.external_references,
        compensation_state=(
            result.state
            if result.state in {ExecutionState.COMPENSATION_REQUIRED, ExecutionState.COMPENSATED}
            else None
        ),
    )


def _execution_receipt_from_audit(
    audit: ExecutionAuditModel, selected_hash: str
) -> ExecutionReceipt:
    state = ExecutionState(audit.state)
    return ExecutionReceipt(
        receipt_id=audit.id,
        organization_id=audit.organization_id,
        run_id=audit.planning_run_id,
        selected_plan_hash=selected_hash,
        idempotency_key=audit.idempotency_key,
        execution_state=state,
        duplicate=False,
        executed_at=audit.completed_at or audit.attempted_at or audit.requested_at,
        external_references=tuple(audit.external_references),
        compensation_state=(
            state
            if state in {ExecutionState.COMPENSATION_REQUIRED, ExecutionState.COMPENSATED}
            else None
        ),
    )


def _product_error(code: ProductErrorCode, message: str) -> ProductServiceError:
    from datetime import UTC, datetime

    return ProductServiceError(
        ProductError(
            code=code,
            message=message,
            retryable=False,
            occurred_at=datetime.now(UTC),
        )
    )
