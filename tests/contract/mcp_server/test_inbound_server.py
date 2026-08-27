"""Contract tests for the thin inbound MCP adapter."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from civitas.contracts.enums import ExecutionState, JuryState
from civitas.contracts.mcp_product import (
    ApprovalChallenge,
    ApprovalReceipt,
    ApprovedTotals,
    ApproveExecutionResponse,
    BusinessImpact,
    DecisionSummary,
    ExecuteApprovedPlanResponse,
    ExecutionAuditResponse,
    ExecutionReceipt,
    IntegritySummary,
    PlanningRun,
    PlanningRunResponse,
    PlanningRunStatus,
    PlanProcurementGoalRequest,
    PrepareExecutionResponse,
    ProductError,
    ProductErrorCode,
    ProductServiceError,
)
from civitas.mcp_server import InboundMCPServer, StaticIdentityProvider
from civitas.ports.identity import OperatorContext

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)
PLAN_HASH = "a" * 64


def _context() -> OperatorContext:
    return OperatorContext(
        organization_id="org-1",
        operator_id="operator-1",
        authentication_subject="local-demo",
        authenticated_at=NOW,
    )


def _run() -> PlanningRun:
    return PlanningRun(
        organization_id="org-1",
        run_id="run-1",
        status=PlanningRunStatus.READY_FOR_APPROVAL,
        policy_version="integrity-v1",
        created_at=NOW,
        updated_at=NOW,
        selected_plan_hash=PLAN_HASH,
    )


def _decision() -> DecisionSummary:
    return DecisionSummary(
        organization_id="org-1",
        run_id="run-1",
        status=PlanningRunStatus.READY_FOR_APPROVAL,
        policy_version="integrity-v1",
        generated_at=NOW,
        selected_plan_id="plan-1",
        selected_plan_hash=PLAN_HASH,
        business_impact=BusinessImpact(
            currency="USD",
            total_landed_cost=Decimal("12.00"),
            expected_waste_value=Decimal("0"),
            shortage_base_units=0,
            procurement_line_count=1,
            distribution_line_count=0,
        ),
        integrity=IntegritySummary(
            score=90,
            state=JuryState.APPROVE,
            hard_gates_passed=True,
        ),
    )


class FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def plan_procurement_goal(
        self, context: OperatorContext, request: object
    ) -> PlanningRunResponse:
        self.calls.append(("plan_procurement_goal", context.organization_id))
        return PlanningRunResponse(run=_run())

    async def get_planning_run(
        self, context: OperatorContext, request: object
    ) -> PlanningRunResponse:
        self.calls.append(("get_planning_run", context.organization_id))
        return PlanningRunResponse(run=_run())

    async def get_decision_summary(
        self, context: OperatorContext, request: object
    ) -> DecisionSummary:
        self.calls.append(("get_decision_summary", context.organization_id))
        return _decision()

    async def prepare_execution(
        self, context: OperatorContext, request: object
    ) -> PrepareExecutionResponse:
        self.calls.append(("prepare_execution", context.organization_id))
        return PrepareExecutionResponse(
            decision=_decision(),
            challenge=ApprovalChallenge(
                challenge_id="challenge-1",
                challenge_secret="safe-test-challenge-secret",
                organization_id="org-1",
                operator_id="operator-1",
                run_id="run-1",
                selected_plan_hash=PLAN_HASH,
                policy_version="integrity-v1",
                approved_totals=ApprovedTotals(
                    currency="USD", maximum_landed_cost=Decimal("12"),
                    maximum_procurement_lines=1,
                    maximum_distribution_lines=0,
                ),
                issued_at=NOW,
                expires_at=NOW + timedelta(minutes=5),
            ),
        )

    async def approve_execution(
        self, context: OperatorContext, request: object
    ) -> ApproveExecutionResponse:
        self.calls.append(("approve_execution", context.organization_id))
        return ApproveExecutionResponse(receipt=self._approval_receipt())

    async def execute_approved_plan(
        self, context: OperatorContext, request: object
    ) -> ExecuteApprovedPlanResponse:
        self.calls.append(("execute_approved_plan", context.organization_id))
        return ExecuteApprovedPlanResponse(execution=self._execution_receipt())

    async def get_execution_audit(
        self, context: OperatorContext, request: object
    ) -> ExecutionAuditResponse:
        self.calls.append(("get_execution_audit", context.organization_id))
        return ExecutionAuditResponse(execution=self._execution_receipt())

    @staticmethod
    def _approval_receipt() -> ApprovalReceipt:
        return ApprovalReceipt(
            receipt_id="approval-1",
            organization_id="org-1",
            operator_id="operator-1",
            run_id="run-1",
            selected_plan_hash=PLAN_HASH,
            policy_version="integrity-v1",
            approved_totals=ApprovedTotals(
                currency="USD", maximum_landed_cost=Decimal("12"),
                maximum_procurement_lines=1,
                maximum_distribution_lines=0,
            ),
            approved_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

    @staticmethod
    def _execution_receipt() -> ExecutionReceipt:
        return ExecutionReceipt(
            receipt_id="execution-1",
            organization_id="org-1",
            run_id="run-1",
            selected_plan_hash=PLAN_HASH,
            idempotency_key="idempotency-1",
            execution_state=ExecutionState.SUCCEEDED,
            duplicate=False,
            executed_at=NOW,
        )


@pytest.fixture
def server() -> InboundMCPServer:
    return InboundMCPServer(FakeService(), StaticIdentityProvider(_context()))


@pytest.mark.asyncio
async def test_tool_schema_is_the_exact_agent_zero_request_contract(
    server: InboundMCPServer,
) -> None:
    tool = server.mcp._tool_manager._tools["plan_procurement_goal"]
    assert tool.parameters == PlanProcurementGoalRequest.model_json_schema()


@pytest.mark.asyncio
async def test_dispatch_delegates_once_and_rejects_unknown_fields(server: InboundMCPServer) -> None:
    result = await server.dispatch("get_decision_summary", {"run_id": "run-1"})
    assert result["run_id"] == "run-1"

    invalid = await server.dispatch("get_decision_summary", {"run_id": "run-1", "sql": "select 1"})
    assert invalid["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_service_errors_are_stable_and_not_stack_traces(server: InboundMCPServer) -> None:
    class RejectingService(FakeService):
        async def get_decision_summary(
            self, context: OperatorContext, request: object
        ) -> DecisionSummary:
            raise ProductServiceError(
                ProductError(
                    code=ProductErrorCode.INVESTIGATION_REQUIRED,
                    message="Fresh supplier evidence is required.",
                    retryable=True,
                    occurred_at=NOW,
                )
            )

    rejecting = InboundMCPServer(RejectingService(), StaticIdentityProvider(_context()))
    result = await rejecting.dispatch("get_decision_summary", {"run_id": "run-1"})
    assert result["code"] == "investigation_required"
    assert "traceback" not in str(result).lower()


@pytest.mark.asyncio
async def test_execute_requires_contractually_valid_receipt_and_idempotency_key(
    server: InboundMCPServer,
) -> None:
    result = await server.dispatch("execute_approved_plan", {"receipt_id": "approval-1"})
    assert result["code"] == "invalid_input"
