"""One deployable composition root for the inbound Civitas MCP service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from civitas.application.live_execution import (
    ExecutionProviderConnectionFactory,
    PersistedApprovalAdapter,
    PersistedApprovedExecutionAdapter,
)
from civitas.application.procurement_facade import (
    ApprovedExecutionPort,
    ProcurementApplicationFacade,
    WorkflowRunStore,
)
from civitas.approval.service import ApprovalService
from civitas.execution.guarded import GuardedExecutionService
from civitas.mcp_server import InboundMCPServer
from civitas.optimization.adapter import OrToolsOptimizer
from civitas.persistence.database import Database
from civitas.persistence.workflow import PostgreSQLWorkflowCheckpointStore
from civitas.persistence.workflow_runs import PostgreSQLWorkflowRunStore
from civitas.ports.mcp import MCPPort
from civitas.runtime.adapters import (
    ControlledBearerIdentity,
    DisabledExecutionPort,
    FailClosedJuryPort,
    SystemClock,
    UUIDGenerator,
)
from civitas.runtime.config import RuntimeSettings
from civitas.worker import DurableWorkflowWorker
from civitas.workflow.orchestrator import ParliamentWorkflow


@dataclass(frozen=True, slots=True)
class ProviderExecutionRuntime:
    """Agent 3 dependencies required to enable Agent 4 provider writes."""

    reads: MCPPort
    connections: ExecutionProviderConnectionFactory
    server_name: str

    def __post_init__(self) -> None:
        if not self.server_name.strip():
            raise ValueError("provider server name is required")


@dataclass(slots=True)
class RuntimeApplication:
    settings: RuntimeSettings
    database: Database
    workflow: ParliamentWorkflow
    checkpoints: PostgreSQLWorkflowCheckpointStore
    workflow_runs: WorkflowRunStore
    executions: ApprovedExecutionPort
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
    provider_execution: ProviderExecutionRuntime | None = None,
) -> RuntimeApplication:
    """Build the live service, enabling writes only with the complete provider boundary."""
    if executions is not None and provider_execution is not None:
        raise ValueError(
            "provide an execution override or provider execution dependencies, not both"
        )
    database = Database(settings.database_url)
    clock = SystemClock()
    ids = UUIDGenerator()
    optimizer = OrToolsOptimizer()
    jury = FailClosedJuryPort(ids=ids, clock=clock)
    workflow = ParliamentWorkflow(optimizer=optimizer, jury=jury, ids=ids, clock=clock)
    checkpoints = PostgreSQLWorkflowCheckpointStore(database.sessions)
    run_store = workflow_runs or PostgreSQLWorkflowRunStore(
        sessions=database.sessions,
        workflow=workflow,
        ids=ids,
        clock=clock,
        policy_version=settings.policy_version,
    )
    approval_service = ApprovalService(
        sessions=database.sessions,
        ids=ids,
        clock=clock,
        secret_pepper=settings.approval_secret_pepper.encode("utf-8"),
    )
    execution_port: ApprovedExecutionPort
    if executions is not None:
        execution_port = executions
    elif provider_execution is not None:
        guarded = GuardedExecutionService(
            sessions=database.sessions,
            mcp=provider_execution.reads,
            ids=ids,
            clock=clock,
            approvals=approval_service,
            server_name=provider_execution.server_name,
        )
        execution_port = PersistedApprovedExecutionAdapter(
            sessions=database.sessions,
            guarded=guarded,
            execution_connections=provider_execution.connections,
            ids=ids,
            clock=clock,
        )
    else:
        execution_port = DisabledExecutionPort()
    facade = ProcurementApplicationFacade(
        workflow_runs=run_store,
        approvals=PersistedApprovalAdapter(approval_service),
        executions=execution_port,
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
        checkpoints=checkpoints,
        workflow_runs=run_store,
        executions=execution_port,
        facade=facade,
        identity=identity,
        mcp_server=server,
    )


def build_worker(settings: RuntimeSettings) -> DurableWorkflowWorker:
    """Build a worker that reads each run's durable autonomy limits."""

    runtime = build_runtime(settings)
    worker_id = settings.worker_id or UUIDGenerator().new_id("worker")
    return DurableWorkflowWorker(
        worker_id=worker_id,
        workflow=runtime.workflow,
        store=runtime.checkpoints,
        clock=SystemClock(),
        lease_for=timedelta(seconds=settings.worker_lease_seconds),
        max_attempts=settings.worker_max_attempts,
        close=runtime.close,
    )


def create_worker() -> DurableWorkflowWorker:
    """Environment-driven factory consumed by ``civitas-worker``."""

    return build_worker(RuntimeSettings.from_env())
