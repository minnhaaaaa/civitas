"""Self-contained, side-effect-safe MCP composition for the Codex demo plugin.

This module deliberately reuses the product facade and the real planning, evidence,
Jury, optimization, approval, and guarded-execution contracts.  Its only substitutes
are an in-memory ledger and the deterministic mock procurement provider, so it can be
launched by Codex over STDIO without production credentials or PostgreSQL.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from civitas.api.app import (
    DemoRunRecord,
    FalseConsensusScenarioState,
    RunEmitter,
    ScenarioJuryPort,
    SystemClock,
    UUIDIDs,
)
from civitas.application.procurement_facade import (
    ProcurementApplicationFacade,
    WorkflowRunSnapshot,
)
from civitas.contracts.common import JsonValue
from civitas.contracts.enums import ExecutionState, WorkflowEventType
from civitas.contracts.execution import ExecutionRequest
from civitas.contracts.mcp_product import (
    ApprovalChallenge,
    ApprovalReceipt,
    ApprovedTotals,
    ApproveExecutionRequest,
    ApproveExecutionResponse,
    BeginProviderConnectionRequest,
    BeginProviderConnectionResponse,
    ConnectionOption,
    ConnectionRecord,
    ConnectionRequirements,
    DecisionSummary,
    EnableSandboxProviderRequest,
    EnableSandboxProviderResponse,
    ExecuteApprovedPlanRequest,
    ExecuteApprovedPlanResponse,
    ExecutionAuditEntry,
    ExecutionAuditResponse,
    ExecutionReceipt,
    GetDecisionSummaryRequest,
    GetExecutionAuditRequest,
    GetPlanningRunRequest,
    ListConnectionsRequest,
    ListConnectionsResponse,
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
    ProviderConnectionState,
    ProviderPurpose,
    ProviderType,
    ResumePlanningRunRequest,
    UpdateSandboxOfferRequest,
    UpdateSandboxOfferResponse,
)
from civitas.contracts.optimization import CandidatePlan, OptimizationRequest
from civitas.contracts.tools import MCPAccessMode
from civitas.contracts.workflow import WorkflowEvent
from civitas.mcp_server.server import (
    MCP_SERVER_INSTRUCTIONS,
    InboundMCPServer,
    StaticIdentityProvider,
)
from civitas.optimization import OrToolsOptimizer
from civitas.ports.identity import OperatorContext
from civitas.workflow import ParliamentWorkflow, WorkflowLimits
from civitas.workflow.models import WorkflowCheckpoint, WorkflowPhase

DEMO_ORGANIZATION_ID = "org-civitas-demo"
DEMO_OPERATOR_ID = "operator-codex-demo"
DEMO_SKU_ID = "sku-apples"
DEMO_WAREHOUSE_ID = "warehouse-north"
DEMO_POLICY_VERSION = "decision-integrity-v1"

DEMO_MCP_INSTRUCTIONS = f"""{MCP_SERVER_INSTRUCTIONS}

This is the local Civitas product demo. For a general request to procure food for
tomorrow, immediately call plan_procurement_goal using the documented sandbox scope:
SKU `sku-apples`, warehouse `warehouse-north`, the operator's timezone, maximum 3
cycles, model-call budget 0, tool-call budget 20, and a five-minute deadline. These
are provider-defined demo identifiers, not invented facts.

Do not inspect workspace files, use Docker, call an HTTP API, or search for another
business system. Civitas MCP is the complete product boundary. A fresh server starts
with no evidence provider: the first planning call must be surfaced as
`connection_required`. Present its provider options and stop for the operator's
choice; never silently enable the sandbox.
"""


@dataclass(slots=True)
class _DemoRun:
    snapshot: WorkflowRunSnapshot
    scenario: FalseConsensusScenarioState
    selected_plan: CandidatePlan | None


class DemoWorkflowRunStore:
    """Run the real false-consensus workflow and retain its safe demo state."""

    def __init__(self, *, ids: UUIDIDs, clock: SystemClock) -> None:
        self._ids = ids
        self._clock = clock
        self._runs: dict[tuple[str, str], _DemoRun] = {}
        self.offer_overrides: dict[str, dict[str, int | str]] = {}

    async def start(
        self,
        *,
        context: OperatorContext,
        run_id: str,
        goal: ProcurementGoal,
        optimization_request: OptimizationRequest,
        limits: WorkflowLimits,
    ) -> WorkflowRunSnapshot:
        del optimization_request
        _validate_demo_scope(goal)
        started_at = self._clock.now()
        record = DemoRunRecord(
            run_id=run_id,
            scenario_id="false-consensus-demo",
            title="False consensus with clean-room dissent",
            started_at=started_at,
            status="running",
        )
        emitter = RunEmitter(record, self._ids, self._clock)
        scenario = FalseConsensusScenarioState(run_id=run_id, ids=self._ids, clock=self._clock)
        scenario.public_server.transport_capacity = [
            {
                "lane_id": "lane-supplier-to-north",
                "destination_warehouse_id": DEMO_WAREHOUSE_ID,
                "sku_id": DEMO_SKU_ID,
                "available_quantity": 20,
                "unit_of_measure": "each",
            }
        ]
        transport = await scenario.public_client.invoke(
            scenario._call("get_transport_capacity", access_mode=MCPAccessMode.READ)
        )
        await emitter.emit(
            WorkflowEventType.EVIDENCE_RECORDED,
            {
                "phase": "evidence",
                "cycle": 1,
                "source_group": "transport_api:lane-supplier-to-north",
                "summary": "Transport capacity was retrieved before optimization.",
                "observation_version": transport.payload.get("observation_version"),
            },
        )
        request = _align_request_to_goal(
            await scenario.retrieve_initial_inputs(emitter=emitter), goal
        )
        workflow = ParliamentWorkflow(
            optimizer=OrToolsOptimizer(),
            jury=ScenarioJuryPort(
                scenario_state=scenario,
                ids=self._ids,
                clock=self._clock,
                emitter=emitter,
            ),
            ids=self._ids,
            clock=self._clock,
            replanner=lambda checkpoint: checkpoint.optimization_request,
        )
        checkpoint = workflow.start(planning_run_id=run_id, optimization_request=request)
        while not checkpoint.completed:
            checkpoint, events = await workflow.advance(checkpoint, limits=limits)
            for event in events:
                await emitter.emit_existing(event)
            if checkpoint.phase is WorkflowPhase.INVESTIGATION and not checkpoint.completed:
                refreshed = _align_request_to_goal(
                    await scenario.refresh_after_investigation(emitter=emitter), goal
                )
                refreshed = _apply_offer_overrides(refreshed, scenario, self.offer_overrides)
                checkpoint = checkpoint.model_copy(update={"optimization_request": refreshed})

        selected = _selected_plan(checkpoint)
        snapshot = WorkflowRunSnapshot(
            organization_id=context.organization_id,
            run_id=run_id,
            policy_version=DEMO_POLICY_VERSION,
            created_at=started_at,
            updated_at=self._clock.now(),
            checkpoint=checkpoint,
            events=tuple(_progress(event) for event in record.events),
        )
        self._runs[(context.organization_id, run_id)] = _DemoRun(
            snapshot=snapshot,
            scenario=scenario,
            selected_plan=selected,
        )
        return snapshot

    async def get(self, *, context: OperatorContext, run_id: str) -> WorkflowRunSnapshot | None:
        run = self._runs.get((context.organization_id, run_id))
        return None if run is None else run.snapshot

    def require(self, organization_id: str, run_id: str) -> _DemoRun:
        run = self._runs.get((organization_id, run_id))
        if run is None:
            raise _product_error(ProductErrorCode.NOT_FOUND, "planning run not found")
        return run


class DemoApprovalLedger:
    """Ephemeral challenge ledger with the same immutable binding as production."""

    def __init__(self, *, ids: UUIDIDs, clock: SystemClock) -> None:
        self._ids = ids
        self._clock = clock
        self._challenges: dict[str, ApprovalChallenge] = {}
        self._receipts: dict[str, ApprovalReceipt] = {}
        self._receipt_by_challenge: dict[str, str] = {}
        self._consumed_keys: dict[str, str] = {}

    async def prepare(
        self, *, context: OperatorContext, summary: DecisionSummary
    ) -> ApprovalChallenge:
        if summary.selected_plan_hash is None or summary.business_impact is None:
            raise _product_error(ProductErrorCode.CONFLICT, "selected plan is unavailable")
        now = self._clock.now()
        challenge = ApprovalChallenge(
            challenge_id=self._ids.new_id("challenge"),
            challenge_secret=secrets.token_urlsafe(24),
            organization_id=context.organization_id,
            operator_id=context.operator_id,
            run_id=summary.run_id,
            selected_plan_hash=summary.selected_plan_hash,
            policy_version=summary.policy_version,
            approved_totals=ApprovedTotals(
                currency=summary.business_impact.currency,
                maximum_landed_cost=summary.business_impact.total_landed_cost,
                maximum_procurement_lines=summary.business_impact.procurement_line_count,
                maximum_distribution_lines=summary.business_impact.distribution_line_count,
            ),
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        self._challenges[challenge.challenge_id] = challenge
        return challenge

    async def approve(
        self, *, context: OperatorContext, request: ApproveExecutionRequest
    ) -> ApprovalReceipt:
        challenge = self._challenges.get(request.challenge_id)
        if challenge is None or (
            challenge.organization_id != context.organization_id
            or challenge.operator_id != context.operator_id
        ):
            raise _product_error(ProductErrorCode.REJECTED_EXECUTION, "approval not found")
        if challenge.expires_at <= self._clock.now():
            raise _product_error(ProductErrorCode.EXPIRED_APPROVAL, "approval challenge expired")
        if not hmac.compare_digest(challenge.challenge_secret, request.challenge_secret):
            raise _product_error(ProductErrorCode.REJECTED_EXECUTION, "approval challenge invalid")
        existing_id = self._receipt_by_challenge.get(challenge.challenge_id)
        if existing_id is not None:
            return self._receipts[existing_id]
        receipt = ApprovalReceipt(
            receipt_id=self._ids.new_id("approval-receipt"),
            organization_id=context.organization_id,
            operator_id=context.operator_id,
            run_id=challenge.run_id,
            selected_plan_hash=challenge.selected_plan_hash,
            policy_version=challenge.policy_version,
            approved_totals=challenge.approved_totals,
            approved_at=self._clock.now(),
            expires_at=challenge.expires_at,
        )
        self._receipts[receipt.receipt_id] = receipt
        self._receipt_by_challenge[challenge.challenge_id] = receipt.receipt_id
        return receipt

    def consume(
        self,
        *,
        context: OperatorContext,
        receipt_id: str,
        idempotency_key: str,
        run_id: str,
        selected_plan_hash: str,
    ) -> ApprovalReceipt:
        receipt = self._receipts.get(receipt_id)
        if receipt is None or (
            receipt.organization_id != context.organization_id
            or receipt.operator_id != context.operator_id
        ):
            raise _product_error(ProductErrorCode.REJECTED_EXECUTION, "approval receipt not found")
        if receipt.expires_at <= self._clock.now():
            raise _product_error(ProductErrorCode.EXPIRED_APPROVAL, "approval receipt expired")
        if receipt.run_id != run_id or receipt.selected_plan_hash != selected_plan_hash:
            raise _product_error(ProductErrorCode.CONFLICT, "approval does not match current plan")
        consumed = self._consumed_keys.get(receipt_id)
        if consumed not in (None, idempotency_key):
            raise _product_error(
                ProductErrorCode.REJECTED_EXECUTION,
                "approval receipt was already consumed by another action",
            )
        self._consumed_keys[receipt_id] = idempotency_key
        return receipt


class DemoExecutionLedger:
    """Adapt guarded mock-provider execution to the public MCP receipt contract."""

    def __init__(
        self,
        *,
        runs: DemoWorkflowRunStore,
        approvals: DemoApprovalLedger,
        ids: UUIDIDs,
        clock: SystemClock,
    ) -> None:
        self._runs = runs
        self._approvals = approvals
        self._ids = ids
        self._clock = clock
        self._by_key: dict[tuple[str, str], ExecutionReceipt] = {}
        self._entries: dict[str, list[ExecutionAuditEntry]] = {}

    async def execute(
        self, *, context: OperatorContext, request: ExecuteApprovedPlanRequest
    ) -> ExecutionReceipt:
        existing = self._by_key.get((context.organization_id, request.idempotency_key))
        if existing is not None:
            duplicate = existing.model_copy(
                update={
                    "duplicate": True,
                    "execution_state": ExecutionState.DUPLICATE,
                    "executed_at": self._clock.now(),
                }
            )
            self._entries[existing.receipt_id].append(
                ExecutionAuditEntry(
                    sequence=len(self._entries[existing.receipt_id]) + 1,
                    occurred_at=self._clock.now(),
                    state=ExecutionState.DUPLICATE,
                    reason_code="duplicate_idempotency_key",
                    detail="Original simulated purchase order preserved.",
                )
            )
            return duplicate

        approval = self._approvals._receipts.get(request.receipt_id)
        if approval is None:
            raise _product_error(ProductErrorCode.REJECTED_EXECUTION, "approval receipt not found")
        run = self._runs.require(context.organization_id, approval.run_id)
        plan = run.selected_plan
        if plan is None or run.snapshot.checkpoint.jury_evaluation is None:
            raise _product_error(
                ProductErrorCode.INVESTIGATION_REQUIRED, "approved plan unavailable"
            )
        plan_hash = _plan_hash(plan)
        self._approvals.consume(
            context=context,
            receipt_id=request.receipt_id,
            idempotency_key=request.idempotency_key,
            run_id=run.snapshot.run_id,
            selected_plan_hash=plan_hash,
        )
        execution_request = ExecutionRequest(
            execution_id=self._ids.new_id("execution"),
            planning_run_id=run.snapshot.run_id,
            approved_plan_id=plan.plan_id,
            jury_evaluation_id=run.snapshot.checkpoint.jury_evaluation.evaluation_id,
            idempotency_key=request.idempotency_key,
            approval_policy_version=approval.policy_version,
            requested_at=self._clock.now(),
            action={"demo_provider": True},
        )
        result = await run.scenario.execution.execute(
            execution_request,
            approved_plan=plan,
            expected_snapshot=run.scenario.expected_revalidation_snapshot(plan),
        )
        receipt = ExecutionReceipt(
            receipt_id=self._ids.new_id("execution-receipt"),
            organization_id=context.organization_id,
            run_id=run.snapshot.run_id,
            selected_plan_hash=plan_hash,
            idempotency_key=request.idempotency_key,
            execution_state=result.state,
            duplicate=False,
            executed_at=result.completed_at or self._clock.now(),
            external_references=result.external_references,
        )
        self._by_key[(context.organization_id, request.idempotency_key)] = receipt
        self._entries[receipt.receipt_id] = [
            ExecutionAuditEntry(
                sequence=1,
                occurred_at=receipt.executed_at,
                state=receipt.execution_state,
                reason_code=result.failure_code,
                detail=result.detail,
            )
        ]
        return receipt

    async def audit(
        self,
        *,
        context: OperatorContext,
        run_id: str,
        receipt_id: str,
        after_sequence: int,
        page_size: int,
    ) -> tuple[ExecutionReceipt, tuple[ExecutionAuditEntry, ...]]:
        receipt = next(
            (
                item
                for (organization_id, _), item in self._by_key.items()
                if organization_id == context.organization_id
                and item.run_id == run_id
                and item.receipt_id == receipt_id
            ),
            None,
        )
        if receipt is None:
            raise _product_error(ProductErrorCode.NOT_FOUND, "execution receipt not found")
        entries = tuple(
            item for item in self._entries.get(receipt_id, ()) if item.sequence > after_sequence
        )[:page_size]
        return receipt, entries


_EVIDENCE_CAPABILITIES = (
    "get_inventory",
    "get_demand",
    "get_supplier_offers",
    "get_lead_times",
    "get_warehouse_capacity",
    "get_transport_capacity",
)
_EXECUTION_CAPABILITIES = ("create_procurement_order",)


class ConnectedDemoService:
    """Conversational provider onboarding around the real product facade."""

    def __init__(
        self,
        *,
        facade: ProcurementApplicationFacade,
        runs: DemoWorkflowRunStore,
        ids: UUIDIDs,
        clock: SystemClock,
    ) -> None:
        self._facade = facade
        self._runs = runs
        self._ids = ids
        self._clock = clock
        self._connections: dict[tuple[str, ProviderPurpose], ConnectionRecord] = {}
        self._pending: dict[tuple[str, str], ProcurementGoal] = {}
        self._pending_created: dict[tuple[str, str], datetime] = {}
        self._offers: dict[str, dict[str, int | str]] = {
            "supplier-a": {
                "unit_cost": "4",
                "lead_time_days": 10,
                "expected_waste_rate": 3,
                "risk": 3,
            },
            "supplier-b": {
                "unit_cost": "7",
                "lead_time_days": 1,
                "expected_waste_rate": 1,
                "risk": 1,
            },
        }
        self._observation_version = 1

    async def list_connections(
        self, context: OperatorContext, request: ListConnectionsRequest
    ) -> ListConnectionsResponse:
        del request
        return ListConnectionsResponse(
            connections=tuple(
                connection
                for (organization_id, _), connection in self._connections.items()
                if organization_id == context.organization_id
            )
        )

    async def begin_provider_connection(
        self, context: OperatorContext, request: BeginProviderConnectionRequest
    ) -> BeginProviderConnectionResponse:
        now = self._clock.now()
        connection = ConnectionRecord(
            connection_id=self._ids.new_id("connection"),
            provider_type=request.provider_type,
            purpose=request.purpose,
            state=ProviderConnectionState.AUTHORIZATION_REQUIRED,
            display_name=("Odoo" if request.provider_type is ProviderType.ODOO else "Remote MCP"),
            live=True,
            capabilities=(),
            write_enabled=False,
        )
        self._connections[(context.organization_id, request.purpose)] = connection
        return BeginProviderConnectionResponse(
            connection=connection,
            authorization_url=f"https://app.civitas.local/connect/{connection.connection_id}",
            expires_at=now + timedelta(minutes=10),
        )

    async def enable_sandbox_provider(
        self, context: OperatorContext, request: EnableSandboxProviderRequest
    ) -> EnableSandboxProviderResponse:
        if not request.acknowledge_simulation:
            raise _product_error(
                ProductErrorCode.INVALID_INPUT,
                "acknowledge that the sandbox does not contact real suppliers or move money",
            )
        capabilities = (
            _EVIDENCE_CAPABILITIES
            if request.purpose is ProviderPurpose.EVIDENCE
            else _EXECUTION_CAPABILITIES
        )
        connection = ConnectionRecord(
            connection_id=f"sandbox-{request.purpose.value}",
            provider_type=ProviderType.CIVITAS_SANDBOX,
            purpose=request.purpose,
            state=ProviderConnectionState.CONNECTED,
            display_name=f"Civitas sandbox {request.purpose.value}",
            live=False,
            capabilities=capabilities,
            write_enabled=request.purpose is ProviderPurpose.EXECUTION,
        )
        self._connections[(context.organization_id, request.purpose)] = connection
        return EnableSandboxProviderResponse(connection=connection)

    async def update_sandbox_offer(
        self, context: OperatorContext, request: UpdateSandboxOfferRequest
    ) -> UpdateSandboxOfferResponse:
        self._require_sandbox(context, ProviderPurpose.EVIDENCE)
        offer = self._offers.get(request.supplier_id)
        if offer is None:
            raise _product_error(ProductErrorCode.NOT_FOUND, "sandbox supplier not found")
        updates = request.model_dump(exclude_none=True, exclude={"contract_version", "supplier_id"})
        offer.update(cast(dict[str, int | str], updates))
        self._runs.offer_overrides[request.supplier_id] = dict(offer)
        self._observation_version += 1
        return UpdateSandboxOfferResponse(
            supplier_id=request.supplier_id,
            unit_cost=Decimal(str(offer["unit_cost"])),
            lead_time_days=int(offer["lead_time_days"]),
            expected_waste_rate=int(offer["expected_waste_rate"]),
            risk=int(offer["risk"]),
            observation_version=f"sandbox-v{self._observation_version}",
        )

    async def plan_procurement_goal(
        self, context: OperatorContext, request: PlanProcurementGoalRequest
    ) -> PlanningRunResponse:
        if not self._connected(context, ProviderPurpose.EVIDENCE):
            _validate_demo_scope(request.goal)
            run_id = self._ids.new_id("run")
            key = (context.organization_id, run_id)
            self._pending[key] = request.goal
            self._pending_created[key] = self._clock.now()
            return self._pending_response(context, run_id)
        return await self._facade.plan_procurement_goal(context, request)

    async def resume_planning_run(
        self, context: OperatorContext, request: ResumePlanningRunRequest
    ) -> PlanningRunResponse:
        self._require_sandbox(context, ProviderPurpose.EVIDENCE)
        key = (context.organization_id, request.run_id)
        goal = self._pending.get(key)
        if goal is None:
            existing = await self._runs.get(context=context, run_id=request.run_id)
            if existing is None:
                raise _product_error(ProductErrorCode.NOT_FOUND, "paused planning run not found")
            return await self._facade.get_planning_run(
                context, GetPlanningRunRequest(run_id=request.run_id)
            )
        snapshot = await self._runs.start(
            context=context,
            run_id=request.run_id,
            goal=goal,
            optimization_request=OptimizationRequest(
                planning_run_id=request.run_id,
                input_data_version=f"sandbox-v{self._observation_version}",
                objectives_version="feasibility-first-v1",
                constraints={},
            ),
            limits=WorkflowLimits(
                max_cycles=goal.maximum_cycles,
                max_tool_calls=goal.tool_call_budget,
                deadline_at=goal.deadline_at,
            ),
        )
        del self._pending[key]
        self._pending_created.pop(key, None)
        return await self._facade.get_planning_run(
            context, GetPlanningRunRequest(run_id=snapshot.run_id)
        )

    async def get_planning_run(
        self, context: OperatorContext, request: GetPlanningRunRequest
    ) -> PlanningRunResponse:
        if (context.organization_id, request.run_id) in self._pending:
            return self._pending_response(context, request.run_id)
        return await self._facade.get_planning_run(context, request)

    async def get_decision_summary(
        self, context: OperatorContext, request: GetDecisionSummaryRequest
    ) -> DecisionSummary:
        if (context.organization_id, request.run_id) in self._pending:
            raise _product_error(
                ProductErrorCode.INVESTIGATION_REQUIRED,
                "connect evidence and resume the paused planning run first",
            )
        return await self._facade.get_decision_summary(context, request)

    async def prepare_execution(
        self, context: OperatorContext, request: PrepareExecutionRequest
    ) -> PrepareExecutionResponse:
        return await self._facade.prepare_execution(context, request)

    async def approve_execution(
        self, context: OperatorContext, request: ApproveExecutionRequest
    ) -> ApproveExecutionResponse:
        approved = await self._facade.approve_execution(context, request)
        if self._connected(context, ProviderPurpose.EXECUTION):
            return approved
        return approved.model_copy(update={"connection_requirements": _execution_requirements()})

    async def execute_approved_plan(
        self, context: OperatorContext, request: ExecuteApprovedPlanRequest
    ) -> ExecuteApprovedPlanResponse:
        if not self._connected(context, ProviderPurpose.EXECUTION):
            raise _product_error(
                ProductErrorCode.INVESTIGATION_REQUIRED,
                "connect a sandbox or live purchase-order provider before execution",
            )
        return await self._facade.execute_approved_plan(context, request)

    async def get_execution_audit(
        self, context: OperatorContext, request: GetExecutionAuditRequest
    ) -> ExecutionAuditResponse:
        return await self._facade.get_execution_audit(context, request)

    def _pending_response(self, context: OperatorContext, run_id: str) -> PlanningRunResponse:
        created_at = self._pending_created[(context.organization_id, run_id)]
        return PlanningRunResponse(
            run=PlanningRun(
                organization_id=context.organization_id,
                run_id=run_id,
                status=PlanningRunStatus.CONNECTION_REQUIRED,
                policy_version=DEMO_POLICY_VERSION,
                created_at=created_at,
                updated_at=self._clock.now(),
                outstanding_investigation=_EVIDENCE_CAPABILITIES,
            ),
            connection_requirements=_evidence_requirements(),
        )

    def _connected(self, context: OperatorContext, purpose: ProviderPurpose) -> bool:
        connection = self._connections.get((context.organization_id, purpose))
        return connection is not None and connection.state is ProviderConnectionState.CONNECTED

    def _require_sandbox(self, context: OperatorContext, purpose: ProviderPurpose) -> None:
        connection = self._connections.get((context.organization_id, purpose))
        if connection is None or connection.provider_type is not ProviderType.CIVITAS_SANDBOX:
            raise _product_error(
                ProductErrorCode.INVESTIGATION_REQUIRED, "sandbox provider is not connected"
            )


def _evidence_requirements() -> ConnectionRequirements:
    return ConnectionRequirements(
        missing_capabilities=_EVIDENCE_CAPABILITIES,
        options=(
            ConnectionOption(
                provider_type=ProviderType.CIVITAS_SANDBOX,
                label="Use mutable Civitas sandbox data",
                purpose=ProviderPurpose.EVIDENCE,
                live=False,
            ),
            ConnectionOption(
                provider_type=ProviderType.REMOTE_MCP,
                label="Connect an operational MCP",
                purpose=ProviderPurpose.EVIDENCE,
                live=True,
            ),
            ConnectionOption(
                provider_type=ProviderType.ODOO,
                label="Connect Odoo",
                purpose=ProviderPurpose.EVIDENCE,
                live=True,
            ),
        ),
        message="Planning is paused until an evidence provider is connected.",
    )


def _execution_requirements() -> ConnectionRequirements:
    return ConnectionRequirements(
        missing_capabilities=_EXECUTION_CAPABILITIES,
        options=(
            ConnectionOption(
                provider_type=ProviderType.CIVITAS_SANDBOX,
                label="Create simulated purchase orders",
                purpose=ProviderPurpose.EXECUTION,
                live=False,
            ),
            ConnectionOption(
                provider_type=ProviderType.REMOTE_MCP,
                label="Connect a live purchasing MCP",
                purpose=ProviderPurpose.EXECUTION,
                live=True,
            ),
            ConnectionOption(
                provider_type=ProviderType.ODOO,
                label="Connect Odoo purchasing",
                purpose=ProviderPurpose.EXECUTION,
                live=True,
            ),
        ),
        message=(
            "Approval is recorded, but execution is paused until a purchase provider is connected."
        ),
    )


def build_demo_server() -> InboundMCPServer:
    clock = SystemClock()
    ids = UUIDIDs()
    runs = DemoWorkflowRunStore(ids=ids, clock=clock)
    approvals = DemoApprovalLedger(ids=ids, clock=clock)
    executions = DemoExecutionLedger(runs=runs, approvals=approvals, ids=ids, clock=clock)
    facade = ProcurementApplicationFacade(
        workflow_runs=runs,
        approvals=approvals,
        executions=executions,
        ids=ids,
        clock=clock,
        policy_version=DEMO_POLICY_VERSION,
    )
    context = OperatorContext(
        organization_id=DEMO_ORGANIZATION_ID,
        operator_id=DEMO_OPERATOR_ID,
        authentication_subject="local-codex-demo",
        authenticated_at=datetime.now(UTC),
        roles=("procurement-operator",),
    )
    service = ConnectedDemoService(facade=facade, runs=runs, ids=ids, clock=clock)
    return InboundMCPServer(
        service,
        StaticIdentityProvider(context),
        instructions=DEMO_MCP_INSTRUCTIONS,
    )


def main() -> int:
    build_demo_server().run_stdio()
    return 0


def _validate_demo_scope(goal: ProcurementGoal) -> None:
    if set(goal.sku_ids) != {DEMO_SKU_ID} or set(goal.warehouse_ids) != {DEMO_WAREHOUSE_ID}:
        raise _product_error(
            ProductErrorCode.INVALID_INPUT,
            f"demo scope is {DEMO_SKU_ID} at {DEMO_WAREHOUSE_ID}",
        )
    objective = goal.objective.casefold()
    if not any(token in objective for token in ("procure", "demand", "food", "cost", "waste")):
        raise _product_error(
            ProductErrorCode.INVALID_INPUT,
            "objective must describe the food-procurement outcome",
        )


def _selected_plan(checkpoint: WorkflowCheckpoint) -> CandidatePlan | None:
    if checkpoint.optimization_result is None or checkpoint.parliament is None:
        return None
    return next(
        (
            plan
            for plan in checkpoint.optimization_result.alternatives
            if plan.plan_id == checkpoint.parliament.selected_plan_id
        ),
        None,
    )


def _align_request_to_goal(
    request: OptimizationRequest, goal: ProcurementGoal
) -> OptimizationRequest:
    """Keep the deterministic scenario data while honoring the operator's horizon."""
    duration = goal.horizon_ends_at - goal.horizon_starts_at
    constraints = dict(request.constraints)
    raw_buckets = constraints.get("buckets")
    if not isinstance(raw_buckets, list) or len(raw_buckets) < 2:
        raise _product_error(ProductErrorCode.CONFLICT, "demo planning buckets unavailable")
    buckets = [dict(item) for item in raw_buckets if isinstance(item, dict)]
    if len(buckets) < 2:
        raise _product_error(ProductErrorCode.CONFLICT, "demo planning buckets malformed")
    buckets[0].update(
        {
            "start": goal.horizon_starts_at.isoformat(),
            "end": goal.horizon_ends_at.isoformat(),
        }
    )
    buckets[1].update(
        {
            "start": goal.horizon_ends_at.isoformat(),
            "end": (goal.horizon_ends_at + duration).isoformat(),
        }
    )
    constraints["buckets"] = cast(JsonValue, buckets)
    return request.model_copy(update={"constraints": constraints})


def _apply_offer_overrides(
    request: OptimizationRequest,
    scenario: FalseConsensusScenarioState,
    overrides: dict[str, dict[str, int | str]],
) -> OptimizationRequest:
    """Apply mutable sandbox facts to both planning and execution provider state."""
    if not overrides:
        return request
    constraints = dict(request.constraints)
    raw_offers = constraints.get("supplier_offers", [])
    if not isinstance(raw_offers, list):
        raise _product_error(ProductErrorCode.CONFLICT, "sandbox supplier offers are malformed")
    offers = [dict(item) for item in raw_offers if isinstance(item, dict)]
    for offer in offers:
        supplier_id = offer.get("supplier_id")
        if not isinstance(supplier_id, str) or supplier_id not in overrides:
            continue
        values = overrides[supplier_id]
        offer["unit_cost"] = values["unit_cost"]
        offer["expected_waste_rate"] = values["expected_waste_rate"]
        offer["risk"] = values["risk"]
        offer["arrival_bucket_id"] = (
            "bucket-day-1" if int(values["lead_time_days"]) <= 1 else "bucket-day-2"
        )
    constraints["supplier_offers"] = cast(JsonValue, offers)
    scenario.public_server.supplier_offers = cast(list[dict[str, object]], offers)
    scenario.public_server.lead_times = [
        {
            "supplier_id": str(item["supplier_id"]),
            "lead_time_days": _integer_value(
                overrides.get(str(item["supplier_id"]), {}).get(
                    "lead_time_days",
                    next(
                        (
                            record["lead_time_days"]
                            for record in scenario.public_server.lead_times
                            if record.get("supplier_id") == item["supplier_id"]
                        ),
                        1,
                    ),
                )
            ),
        }
        for item in offers
    ]
    scenario.public_server.observation_version = f"sandbox-v{len(overrides) + 2}"
    return request.model_copy(update={"constraints": constraints})


def _integer_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _product_error(ProductErrorCode.CONFLICT, "sandbox integer value is malformed")
    return int(value)


def _plan_hash(plan: CandidatePlan) -> str:
    payload = json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _progress(event: WorkflowEvent) -> PlanningProgress:
    raw_reason_codes = event.payload.get("reason_codes", [])
    reason_codes = (
        tuple(str(code) for code in raw_reason_codes if isinstance(code, str))
        if isinstance(raw_reason_codes, list)
        else ()
    )
    phase = event.payload.get("phase")
    return PlanningProgress(
        sequence=event.sequence,
        occurred_at=event.occurred_at,
        phase=phase if isinstance(phase, str) else event.event_type.value,
        message=str(
            event.payload.get("note") or event.payload.get("summary") or event.event_type.value
        ),
        reason_codes=reason_codes,
    )


def _product_error(code: ProductErrorCode, message: str) -> ProductServiceError:
    return ProductServiceError(
        ProductError(
            code=code,
            message=message,
            retryable=False,
            occurred_at=datetime.now(UTC),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
