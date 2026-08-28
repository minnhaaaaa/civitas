"""Small adapters joining existing application ports at the composition boundary."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime

from civitas.approval.service import (
    ApprovalError,
    ApprovalService,
    ChangedPlanError,
    ExpiredApprovalError,
)
from civitas.contracts.jury import JuryEvaluation, JuryRequest
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
from civitas.evidence.jury import JuryEvaluator, JuryInputs
from civitas.ports.identity import OperatorContext


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UUIDGenerator:
    def new_id(self, namespace: str) -> str:
        return f"{namespace}-{uuid.uuid4()}"


class ControlledBearerIdentity:
    """Single-tenant bearer binding; OAuth identity can replace this adapter."""

    def __init__(
        self,
        *,
        token: str,
        organization_id: str,
        operator_id: str,
        subject: str,
        roles: tuple[str, ...],
        clock: SystemClock,
    ) -> None:
        self._token = token
        self._organization_id = organization_id
        self._operator_id = operator_id
        self._subject = subject
        self._roles = roles
        self._clock = clock

    async def resolve(self, token: str) -> OperatorContext | None:
        if not hmac.compare_digest(token, self._token):
            return None
        return self.context()

    async def current_operator(self) -> OperatorContext:
        return self.context()

    def context(self) -> OperatorContext:
        return OperatorContext(
            organization_id=self._organization_id,
            operator_id=self._operator_id,
            authentication_subject=self._subject,
            authenticated_at=self._clock.now(),
            roles=self._roles,
        )


class FailClosedJuryPort:
    """Safe baseline until durable evidence/Dissent inputs are supplied by Agent 6."""

    def __init__(self, *, ids: UUIDGenerator, clock: SystemClock) -> None:
        self._ids = ids
        self._clock = clock
        self._evaluator = JuryEvaluator()

    async def evaluate(self, request: JuryRequest) -> JuryEvaluation:
        return self._evaluator.evaluate(
            request,
            JuryInputs(claims=(), evidence=(), dissent=None),
            evaluation_id=self._ids.new_id("jury"),
            calculated_at=self._clock.now(),
        )


class ApprovalFacadeAdapter:
    def __init__(self, service: ApprovalService) -> None:
        self._service = service

    async def prepare(
        self, *, context: OperatorContext, summary: DecisionSummary
    ) -> ApprovalChallenge:
        impact = summary.business_impact
        if summary.selected_plan_hash is None or impact is None:
            raise _product_error(ProductErrorCode.CONFLICT, "selected plan is unavailable")
        return await self._service.issue(
            context=context,
            run_id=summary.run_id,
            selected_plan_hash=summary.selected_plan_hash,
            policy_version=summary.policy_version,
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


class DisabledExecutionPort:
    """Explicit safe boundary until Agent 4 supplies approved provider execution."""

    async def execute(
        self, *, context: OperatorContext, request: ExecuteApprovedPlanRequest
    ) -> ExecutionReceipt:
        del context, request
        raise _product_error(
            ProductErrorCode.REJECTED_EXECUTION,
            "outbound execution is not configured for this deployment",
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
        del context, run_id, receipt_id, after_sequence, page_size
        raise _product_error(ProductErrorCode.NOT_FOUND, "execution receipt not found")


def _product_error(code: ProductErrorCode, message: str) -> ProductServiceError:
    return ProductServiceError(
        ProductError(
            code=code,
            message=message,
            retryable=False,
            occurred_at=datetime.now(UTC),
        )
    )
