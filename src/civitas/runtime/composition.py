"""One deployable composition root for the inbound Civitas MCP service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from civitas.api.audit_viewer import audit_viewer_routes
from civitas.application.audit_viewer import PostgreSQLAuditViewerService
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
from civitas.application.planning_inputs import ProviderPlanningInputAssembler
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
)
from civitas.identity.context import AuthenticatedPrincipal, derive_operator_context
from civitas.integrations.mcp import CleanRoomNamespace
from civitas.integrations.providers import ProviderConnections
from civitas.mcp_server import InboundMCPServer, StaticIdentityProvider
from civitas.optimization.adapter import OrToolsOptimizer
from civitas.persistence.database import Database
from civitas.persistence.evidence import PostgreSQLEvidenceLedger
from civitas.persistence.health import PostgreSQLServiceHealthStore
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
from civitas.runtime.health import RuntimeHealth, install_operational_surface
from civitas.runtime.observability import LoggingAuthenticationAuditSink, MetricsRegistry
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
    audit_viewer: PostgreSQLAuditViewerService | None
    health: RuntimeHealth
    metrics: MetricsRegistry
    _closed: bool = False
    _http_app: Any = None

    def http_app(self) -> Any:
        if self._http_app is not None:
            return self._http_app
        app = self.mcp_server.streamable_http_app(
            verifier=self.identity,
            rate_limiter=self.rate_limiter,
            audit_sink=self.authentication_audit,
        )
        if self.audit_viewer is not None:
            app.routes.extend(
                audit_viewer_routes(
                    service=self.audit_viewer,
                    rate_limiter=self.rate_limiter,
                )
            )
        install_operational_surface(
            app,
            health=self.health,
            metrics=self.metrics,
            settings=self.settings,
        )
        app_with_lifecycle: Any = app
        original_lifespan = app_with_lifecycle.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application: Any) -> AsyncIterator[object]:
            await self.health.start()
            try:
                async with original_lifespan(application) as state:
                    yield state
            finally:
                await self.close()

        app_with_lifecycle.router.lifespan_context = lifespan
        self._http_app = app
        return app

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.health.stop()
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
    evidence_ledger: PostgreSQLEvidenceLedger | None = None
    input_assembler: ProviderPlanningInputAssembler | None = None
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
            tool_budget=6,
        )
        investigator = JuryDirectedInvestigator(
            reader=provider_planning.evidence,
            ledger=evidence_ledger,
            ids=ids,
            server_name=provider_planning.server_name,
            organization_id=settings.organization_id,
        )
        input_assembler = ProviderPlanningInputAssembler(
            reader=provider_planning.evidence,
            ids=ids,
            server_name=provider_planning.server_name,
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
        input_assembler=input_assembler,
        evidence_ledger=evidence_ledger,
    )
    approval_service = ApprovalService(
        sessions=database.sessions,
        ids=ids,
        clock=clock,
        secret_pepper=settings.approval_secret_pepper.encode("utf-8"),
    )
    audit_viewer = None
    if settings.audit_viewer_enabled:
        assert settings.audit_link_secret is not None
        assert settings.audit_viewer_base_url is not None
        audit_viewer = PostgreSQLAuditViewerService(
            sessions=database.sessions,
            ids=ids,
            clock=clock,
            secret=settings.audit_link_secret.encode("utf-8"),
            viewer_base_url=settings.audit_viewer_base_url,
            ttl=timedelta(seconds=settings.audit_link_ttl_seconds),
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
        audit_links=audit_viewer,
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
    effective_audit = authentication_audit or LoggingAuthenticationAuditSink()
    server = InboundMCPServer(
        facade,
        StaticIdentityProvider(operator_context),
        authorizer=RoleAuthorizer(),
    )
    service_health = PostgreSQLServiceHealthStore(database.sessions)
    health = RuntimeHealth(
        store=service_health,
        clock=clock,
        settings=settings,
        service_id=ids.new_id("mcp-server"),
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
        audit_viewer=audit_viewer,
        health=health,
        metrics=MetricsRegistry(),
    )


def build_worker(
    settings: RuntimeSettings,
    *,
    provider_planning: ProviderPlanningRuntime | None = None,
) -> DurableWorkflowWorker:
    """Build a worker that reads each run's durable autonomy limits."""

    runtime = build_runtime(settings, provider_planning=provider_planning)
    worker_id = settings.worker_id or UUIDGenerator().new_id("worker")
    service_health = PostgreSQLServiceHealthStore(runtime.database.sessions)
    clock = SystemClock()
    return DurableWorkflowWorker(
        worker_id=worker_id,
        workflow=runtime.workflow,
        store=runtime.checkpoints,
        clock=clock,
        lease_for=timedelta(seconds=settings.worker_lease_seconds),
        max_attempts=settings.worker_max_attempts,
        close=runtime.close,
        heartbeat=lambda: service_health.heartbeat(
            service_id=worker_id,
            service_kind="worker",
            now=clock.now(),
        ),
        stopping=lambda: service_health.heartbeat(
            service_id=worker_id,
            service_kind="worker",
            now=clock.now(),
            state="stopping",
        ),
    )


async def create_worker() -> DurableWorkflowWorker:
    """Environment-driven factory consumed by ``civitas-worker``."""

    from civitas.runtime.bootstrap import load_provider_runtime
    from civitas.runtime.observability import configure_logging

    settings = RuntimeSettings.from_env()
    configure_logging(
        service="civitas-worker",
        environment=settings.environment,
        level=settings.log_level,
        log_format=settings.log_format,
    )
    provider = await load_provider_runtime(settings)
    return build_worker(
        settings,
        provider_planning=None if provider is None else provider.planning,
    )
