"""One deployable composition root for the inbound Civitas MCP service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from civitas.application.investigation import (
    DurableCleanRoomJury,
    EvidenceReader,
    JuryDirectedInvestigator,
)
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
from civitas.identity import (
    BearerCredential,
    FixedWindowRateLimiter,
    HashedBearerVerifier,
    RateLimiter,
    RoleAuthorizer,
)
from civitas.identity.audit import (
    AuthenticationAuditSink,
    NullAuthenticationAuditSink,
)
from civitas.identity.context import AuthenticatedPrincipal, derive_operator_context
from civitas.integrations.mcp import CleanRoomNamespace
from civitas.integrations.providers import ProviderConnections
from civitas.mcp_server import InboundMCPServer, StaticIdentityProvider
from civitas.optimization.adapter import OrToolsOptimizer
from civitas.persistence.database import Database
from civitas.persistence.evidence import PostgreSQLEvidenceLedger
from civitas.persistence.workflow import PostgreSQLWorkflowCheckpointStore
from civitas.persistence.workflow_runs import PostgreSQLWorkflowRunStore
from civitas.ports.identity import OperatorContext
from civitas.ports.investigation import PlanningInvestigator
from civitas.ports.jury import JuryPort
from civitas.ports.mcp import MCPPort
from civitas.runtime.adapters import (
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


@dataclass(frozen=True, slots=True)
class ProviderPlanningRuntime:
    """Read-only planning and isolated Dissent connections from Agent 3."""

    evidence: EvidenceReader
    dissent: EvidenceReader
    dissent_namespace: CleanRoomNamespace
    server_name: str

    def __post_init__(self) -> None:
        if not self.server_name.strip():
            raise ValueError("provider server name is required")

    @classmethod
    def from_connections(cls, connections: ProviderConnections) -> ProviderPlanningRuntime:
        """Bind the exact clean-room namespace and credential-isolated Agent 3 clients."""

        return cls(
            evidence=connections.evidence,
            dissent=connections.dissent_evidence,
            dissent_namespace=connections.dissent.namespace,
            server_name=connections.evidence.manifest.server_name,
        )


@dataclass(slots=True)
class RuntimeApplication:
    settings: RuntimeSettings
    database: Database
    workflow: ParliamentWorkflow
    checkpoints: PostgreSQLWorkflowCheckpointStore
    workflow_runs: WorkflowRunStore
    executions: ApprovedExecutionPort
    facade: ProcurementApplicationFacade
    identity: HashedBearerVerifier
    operator_context: OperatorContext
    rate_limiter: RateLimiter
    authentication_audit: AuthenticationAuditSink
    mcp_server: InboundMCPServer

    def http_app(self) -> Any:
        app = self.mcp_server.streamable_http_app(
            verifier=self.identity,
            rate_limiter=self.rate_limiter,
            audit_sink=self.authentication_audit,
        )
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
    provider_planning: ProviderPlanningRuntime | None = None,
    rate_limiter: RateLimiter | None = None,
    authentication_audit: AuthenticationAuditSink | None = None,
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
    investigator: PlanningInvestigator | None = None
    jury: JuryPort
    if provider_planning is None:
        jury = FailClosedJuryPort(ids=ids, clock=clock)
    else:
        evidence_ledger = PostgreSQLEvidenceLedger(database.sessions, ids=ids)
        jury = DurableCleanRoomJury(
            dissent_reader=provider_planning.dissent,
            ledger=evidence_ledger,
            ids=ids,
            clock=clock,
            server_name=provider_planning.server_name,
            organization_id=settings.organization_id,
            clean_room_namespace=provider_planning.dissent_namespace,
        )
        investigator = JuryDirectedInvestigator(
            reader=provider_planning.evidence,
            ledger=evidence_ledger,
            ids=ids,
            server_name=provider_planning.server_name,
            organization_id=settings.organization_id,
        )
    workflow = ParliamentWorkflow(
        optimizer=optimizer,
        jury=jury,
        ids=ids,
        clock=clock,
        investigator=investigator,
    )
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
    operator_context = derive_operator_context(
        AuthenticatedPrincipal(
            organization_id=settings.organization_id,
            operator_id=settings.operator_id,
            subject=settings.operator_subject,
            authenticated_at=clock.now(),
            roles=settings.operator_roles,
        )
    )
    identity = HashedBearerVerifier(
        (
            BearerCredential.from_secret(
                settings.bearer_token,
                organization_id=settings.organization_id,
                operator_id=settings.operator_id,
                subject=settings.operator_subject,
                roles=settings.operator_roles,
                expires_at=clock.now() + timedelta(seconds=settings.bearer_ttl_seconds),
            ),
        )
    )
    effective_rate_limiter = rate_limiter or FixedWindowRateLimiter(
        requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    effective_audit = authentication_audit or NullAuthenticationAuditSink()
    server = InboundMCPServer(
        facade,
        StaticIdentityProvider(operator_context),
        authorizer=RoleAuthorizer(),
    )
    return RuntimeApplication(
        settings=settings,
        database=database,
        workflow=workflow,
        checkpoints=checkpoints,
        workflow_runs=run_store,
        executions=execution_port,
        facade=facade,
        identity=identity,
        operator_context=operator_context,
        rate_limiter=effective_rate_limiter,
        authentication_audit=effective_audit,
        mcp_server=server,
    )


def build_worker(
    settings: RuntimeSettings,
    *,
    provider_planning: ProviderPlanningRuntime | None = None,
) -> DurableWorkflowWorker:
    """Build a worker that reads each run's durable autonomy limits."""

    runtime = build_runtime(settings, provider_planning=provider_planning)
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
