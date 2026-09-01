"""Application-service port consumed by MCP and HTTP transports."""

from typing import Protocol

from civitas.contracts.mcp_product import (
    ApproveExecutionRequest,
    ApproveExecutionResponse,
    BeginProviderConnectionRequest,
    BeginProviderConnectionResponse,
    DecisionSummary,
    EnableSandboxProviderRequest,
    EnableSandboxProviderResponse,
    ExecuteApprovedPlanRequest,
    ExecuteApprovedPlanResponse,
    ExecutionAuditResponse,
    GetDecisionSummaryRequest,
    GetExecutionAuditRequest,
    GetPlanningRunRequest,
    ListConnectionsRequest,
    ListConnectionsResponse,
    PlanningRunResponse,
    PlanProcurementGoalRequest,
    PrepareExecutionRequest,
    PrepareExecutionResponse,
    ResumePlanningRunRequest,
    UpdateSandboxOfferRequest,
    UpdateSandboxOfferResponse,
)
from civitas.ports.identity import OperatorContext


class ProductService(Protocol):
    """The sole application boundary for intent-level procurement operations."""

    async def list_connections(
        self, context: OperatorContext, request: ListConnectionsRequest
    ) -> ListConnectionsResponse: ...

    async def begin_provider_connection(
        self, context: OperatorContext, request: BeginProviderConnectionRequest
    ) -> BeginProviderConnectionResponse: ...

    async def enable_sandbox_provider(
        self, context: OperatorContext, request: EnableSandboxProviderRequest
    ) -> EnableSandboxProviderResponse: ...

    async def update_sandbox_offer(
        self, context: OperatorContext, request: UpdateSandboxOfferRequest
    ) -> UpdateSandboxOfferResponse: ...

    async def resume_planning_run(
        self, context: OperatorContext, request: ResumePlanningRunRequest
    ) -> PlanningRunResponse: ...

    async def plan_procurement_goal(
        self,
        context: OperatorContext,
        request: PlanProcurementGoalRequest,
    ) -> PlanningRunResponse: ...

    async def get_planning_run(
        self,
        context: OperatorContext,
        request: GetPlanningRunRequest,
    ) -> PlanningRunResponse: ...

    async def get_decision_summary(
        self,
        context: OperatorContext,
        request: GetDecisionSummaryRequest,
    ) -> DecisionSummary: ...

    async def prepare_execution(
        self,
        context: OperatorContext,
        request: PrepareExecutionRequest,
    ) -> PrepareExecutionResponse: ...

    async def approve_execution(
        self,
        context: OperatorContext,
        request: ApproveExecutionRequest,
    ) -> ApproveExecutionResponse: ...

    async def execute_approved_plan(
        self,
        context: OperatorContext,
        request: ExecuteApprovedPlanRequest,
    ) -> ExecuteApprovedPlanResponse: ...

    async def get_execution_audit(
        self,
        context: OperatorContext,
        request: GetExecutionAuditRequest,
    ) -> ExecutionAuditResponse: ...
