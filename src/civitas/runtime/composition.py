"""One deployable composition root for the inbound Civitas MCP service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from civitas.application.procurement_facade import (
    ApprovedExecutionPort,
    ProcurementApplicationFacade,
    WorkflowRunStore,
)
from civitas.approval.service import ApprovalService
from civitas.mcp_server import InboundMCPServer
from civitas.optimization.adapter import OrToolsOptimizer
from civitas.persistence.database import Database
from civitas.runtime.adapters import (
    ApprovalFacadeAdapter,
    ControlledBearerIdentity,
    DisabledExecutionPort,
    FailClosedJuryPort,
    InlineWorkflowRunStore,
    SystemClock,
    UUIDGenerator,
)
from civitas.runtime.config import RuntimeSettings
from civitas.workflow.orchestrator import ParliamentWorkflow


@dataclass(slots=True)
class RuntimeApplication:
    settings: RuntimeSettings
    database: Database
    workflow: ParliamentWorkflow
    facade: ProcurementApplicationFacade
    identity: ControlledBearerIdentity
    mcp_server: InboundMCPServer

    def http_app(self) -> Any:
        app = self.mcp_server.streamable_http_app(self.identity.resolve)
        app_with_lifecycle: Any = app
        app_with_lifecycle.add_event_handler("shutdown", self.close)
        return app

    async def close(self) -> None:
        await self.database.dispose()


def build_runtime(
    settings: RuntimeSettings,
    *,
    workflow_runs: WorkflowRunStore | None = None,
    executions: ApprovedExecutionPort | None = None,
) -> RuntimeApplication:
    """Build all current concrete services, with narrow seams for Agents 2 and 4."""
    database = Database(settings.database_url)
    clock = SystemClock()
    ids = UUIDGenerator()
    optimizer = OrToolsOptimizer()
    jury = FailClosedJuryPort(ids=ids, clock=clock)
    workflow = ParliamentWorkflow(optimizer=optimizer, jury=jury, ids=ids, clock=clock)
    run_store = workflow_runs or InlineWorkflowRunStore(
        workflow=workflow, clock=clock, policy_version=settings.policy_version
    )
    approval_service = ApprovalService(
        sessions=database.sessions,
        ids=ids,
        clock=clock,
        secret_pepper=settings.approval_secret_pepper.encode("utf-8"),
    )
    facade = ProcurementApplicationFacade(
        workflow_runs=run_store,
        approvals=ApprovalFacadeAdapter(approval_service),
        executions=executions or DisabledExecutionPort(),
        ids=ids,
        clock=clock,
        policy_version=settings.policy_version,
    )
    identity = ControlledBearerIdentity(
        token=settings.bearer_token,
        organization_id=settings.organization_id,
        operator_id=settings.operator_id,
        subject=settings.operator_subject,
        roles=settings.operator_roles,
        clock=clock,
    )
    server = InboundMCPServer(facade, identity.current_operator)
    return RuntimeApplication(
        settings=settings,
        database=database,
        workflow=workflow,
        facade=facade,
        identity=identity,
        mcp_server=server,
    )
