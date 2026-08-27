"""FastAPI app for the Civitas end-to-end integration demo."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from tools.mock_mcp.server import MockProcurementMCPServer

from civitas.contracts import (
    ClaimScope,
    EvidenceIdentity,
    EvidenceOrigin,
    EvidenceRecord,
    ExecutionRequest,
    SSEPayload,
    TypedClaim,
    WorkflowEvent,
    WorkflowEventType,
)
from civitas.contracts.common import Contract
from civitas.contracts.enums import JuryState
from civitas.contracts.execution import ExecutionResult
from civitas.contracts.jury import JuryEvaluation, JuryRequest
from civitas.contracts.optimization import CandidatePlan, OptimizationRequest
from civitas.contracts.tools import MCPAccessMode, MCPToolCall
from civitas.evidence import (
    DissentInvestigationPlan,
    DissentProtocol,
    GateFacts,
    JuryEvaluator,
    JuryInputs,
)
from civitas.execution.service import GuardedExecutionService, RevalidationSnapshot
from civitas.integrations import (
    DEFAULT_PROCUREMENT_POLICY,
    DissentMCPClient,
    MCPClient,
    ToolEvidenceMapping,
    clean_room_namespace,
    evidence_from_tool_result,
)
from civitas.optimization import OrToolsOptimizer
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator
from civitas.workflow import ParliamentWorkflow, WorkflowCheckpoint, WorkflowLimits
from civitas.workflow.models import WorkflowPhase


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)


class UUIDIDs(IDGenerator):
    def new_id(self, namespace: str) -> str:
        return f"{namespace}-{uuid4()}"


class CreateDemoRunRequest(Contract):
    scenario_id: str = "false-consensus-demo"


@dataclass(frozen=True, slots=True)
class DemoScenarioSummary:
    scenario_id: str
    title: str
    description: str


@dataclass(slots=True)
class DemoRunRecord:
    run_id: str
    scenario_id: str
    title: str
    started_at: datetime
    status: str = "pending"
    current_cycle: int = 0
    final_state: str | None = None
    events: list[WorkflowEvent] = field(default_factory=list)
    _subscriber_queues: list[asyncio.Queue[WorkflowEvent | None]] = field(default_factory=list)
    _done: asyncio.Event = field(default_factory=asyncio.Event)

    async def publish(self, event: WorkflowEvent) -> None:
        self.events.append(event)
        cycle = event.payload.get("cycle")
        if isinstance(cycle, int) and not isinstance(cycle, bool):
            self.current_cycle = cycle
        for queue in list(self._subscriber_queues):
            await queue.put(event)

    async def close(self) -> None:
        self._done.set()
        for queue in list(self._subscriber_queues):
            await queue.put(None)

    async def stream(self) -> AsyncIterator[WorkflowEvent]:
        queue: asyncio.Queue[WorkflowEvent | None] = asyncio.Queue()
        self._subscriber_queues.append(queue)
        replay = tuple(self.events)
        already_done = self._done.is_set()
        try:
            for event in replay:
                yield event
            if already_done:
                return
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            self._subscriber_queues.remove(queue)


class ScenarioJuryPort:
    def __init__(
        self,
        *,
        scenario_state: FalseConsensusScenarioState,
        ids: IDGenerator,
        clock: Clock,
        emitter: RunEmitter,
    ) -> None:
        self._state = scenario_state
        self._ids = ids
        self._clock = clock
        self._emitter = emitter
        self._evaluator = JuryEvaluator()

    async def evaluate(self, request: JuryRequest) -> JuryEvaluation:
        await self._state.prepare_dissent(emitter=self._emitter)
        inputs = self._state.jury_inputs(request)
        evaluation = self._evaluator.evaluate(
            request,
            inputs,
            evaluation_id=self._ids.new_id("jury-eval"),
            calculated_at=self._clock.now(),
        )
        self._state.record_evaluation(evaluation)
        return evaluation


class RunEmitter:
    def __init__(self, run: DemoRunRecord, ids: IDGenerator, clock: Clock) -> None:
        self._run = run
        self._ids = ids
        self._clock = clock
        self._sequence = 0

    async def emit_existing(self, event: WorkflowEvent) -> None:
        self._sequence += 1
        replay = event.model_copy(update={"sequence": self._sequence})
        await self._run.publish(replay)

    async def emit(
        self,
        event_type: WorkflowEventType,
        payload: dict[str, Any],
        *,
        actor_id: str | None = None,
    ) -> None:
        self._sequence += 1
        event = WorkflowEvent(
            event_id=self._ids.new_id("stream-event"),
            planning_run_id=self._run.run_id,
            sequence=self._sequence,
            event_type=event_type,
            occurred_at=self._clock.now(),
            actor_id=actor_id,
            payload=payload,
        )
        await self._run.publish(event)


class FalseConsensusScenarioState:
    def __init__(self, *, run_id: str, ids: IDGenerator, clock: Clock) -> None:
        self.run_id = run_id
        self.ids = ids
        self.clock = clock
        self.public_server = MockProcurementMCPServer(
            inventory=[
                {
                    "lot_id": "lot-local-1",
                    "sku_id": "sku-apples",
                    "warehouse_id": "warehouse-north",
                    "quantity": 2,
                }
            ],
            demand=[
                {
                    "demand_id": "demand-day-1",
                    "sku_id": "sku-apples",
                    "warehouse_id": "warehouse-north",
                    "bucket_id": "bucket-day-1",
                    "quantity": 6,
                    "priority": 3,
                    "minimum_service": 4,
                }
            ],
            supplier_offers=[
                {
                    "offer_id": "offer-a-stale",
                    "supplier_id": "supplier-a",
                    "sku_id": "sku-apples",
                    "destination_warehouse_id": "warehouse-north",
                    "arrival_bucket_id": "bucket-day-1",
                    "capacity": 4,
                    "unit_cost": 4,
                    "pack_size": 1,
                    "minimum_order": 1,
                    "risk": 1,
                    "expected_waste_rate": 1,
                },
                {
                    "offer_id": "offer-b-slow",
                    "supplier_id": "supplier-b",
                    "sku_id": "sku-apples",
                    "destination_warehouse_id": "warehouse-north",
                    "arrival_bucket_id": "bucket-day-2",
                    "capacity": 4,
                    "unit_cost": 7,
                    "pack_size": 1,
                    "minimum_order": 1,
                    "risk": 1,
                    "expected_waste_rate": 1,
                },
            ],
            lead_times=[
                {"supplier_id": "supplier-a", "lead_time_days": 1},
                {"supplier_id": "supplier-b", "lead_time_days": 3},
            ],
            warehouse_capacity=[
                {"warehouse_id": "warehouse-north", "remaining_capacity_units": 20}
            ],
            observation_version="public-v1",
        )
        self.audit_server = MockProcurementMCPServer(
            lead_times=[
                {"supplier_id": "supplier-a", "lead_time_days": 10},
                {"supplier_id": "supplier-b", "lead_time_days": 1},
            ],
            observation_version="audit-v1",
        )
        self.public_client = MCPClient(
            transport=self.public_server,
            policy=DEFAULT_PROCUREMENT_POLICY,
        )
        self.dissent_client = DissentMCPClient(
            transport=self.audit_server,
            namespace=clean_room_namespace(run_id),
        )
        self.execution = GuardedExecutionService(
            mcp=self.public_client,
            ids=ids,
            clock=clock,
            server_name="mock-procurement",
        )
        self.public_claims: dict[str, TypedClaim] = {}
        self.public_evidence: dict[str, EvidenceRecord] = {}
        self.dissent_claims: dict[str, TypedClaim] = {}
        self.dissent_evidence: dict[str, EvidenceRecord] = {}
        self.current_phase = "initial"
        self.latest_evaluation: JuryEvaluation | None = None
        self.expected_snapshot: RevalidationSnapshot | None = None

    async def retrieve_initial_inputs(self, *, emitter: RunEmitter) -> OptimizationRequest:
        for tool_name in (
            "get_inventory",
            "get_demand",
            "get_supplier_offers",
            "get_lead_times",
            "get_warehouse_capacity",
        ):
            result = await self.public_client.invoke(
                self._call(tool_name, access_mode=MCPAccessMode.READ)
            )
            if tool_name == "get_inventory":
                claim = self._claim(
                    "claim.inventory.local",
                    subject="warehouse-north",
                    predicate="inventory_balance",
                    value=2,
                    warehouse_id="warehouse-north",
                    human_summary="Warehouse North has 2 units on hand.",
                )
                evidence = evidence_from_tool_result(
                    evidence_id="evidence.inventory.local",
                    call=self._call(tool_name, access_mode=MCPAccessMode.READ),
                    result=result,
                    mapping=ToolEvidenceMapping(
                        canonical_source_type="inventory_api",
                        canonical_source_id="warehouse-north-ledger",
                    ),
                    claim_ids=(claim.claim_id,),
                )
                self.public_claims[claim.claim_id] = claim
                self.public_evidence[evidence.evidence_id] = evidence
                await emitter.emit(
                    WorkflowEventType.EVIDENCE_RECORDED,
                    self._evidence_payload(evidence, "Inventory imported for planning."),
                )
            elif tool_name == "get_supplier_offers":
                lead_claim = self._claim(
                    "claim.supplier_a.lead_time",
                    subject="supplier-a",
                    predicate="lead_time",
                    value=1,
                    supplier_id="supplier-a",
                    human_summary="Supplier A is expected to arrive in 1 day.",
                )
                offer_claim = self._claim(
                    "claim.supplier_a.unit_price",
                    subject="supplier-a",
                    predicate="unit_price",
                    value=4,
                    supplier_id="supplier-a",
                    human_summary="Supplier A has the cheapest visible unit price.",
                )
                evidence = evidence_from_tool_result(
                    evidence_id="evidence.supplier_a.stale_offer",
                    call=self._call(tool_name, access_mode=MCPAccessMode.READ),
                    result=result,
                    mapping=ToolEvidenceMapping(
                        canonical_source_type="supplier_api",
                        canonical_source_id="supplier-a-master",
                    ),
                    claim_ids=(lead_claim.claim_id, offer_claim.claim_id),
                )
                echo = EvidenceRecord(
                    evidence_id="evidence.supplier_a.echo",
                    claim_ids=(lead_claim.claim_id,),
                    identity=EvidenceIdentity(
                        canonical_source_id="agent-synthesis",
                        canonical_source_type="agent_summary",
                        mcp_server=None,
                        tool_name=None,
                        normalized_arguments={},
                        retrieved_at=self.clock.now(),
                        observation_version="echo-v1",
                        raw_response_sha256="b" * 64,
                    ),
                    origin=EvidenceOrigin.AGENT_DERIVED,
                    agent_id="cost-agent",
                    content_summary="Cost agent repeated supplier A's lead-time assumption.",
                    derived_from=(evidence.evidence_id,),
                )
                self.public_claims[lead_claim.claim_id] = lead_claim
                self.public_claims[offer_claim.claim_id] = offer_claim
                self.public_evidence[evidence.evidence_id] = evidence
                self.public_evidence[echo.evidence_id] = echo
                await emitter.emit(
                    WorkflowEventType.EVIDENCE_RECORDED,
                    self._evidence_payload(
                        evidence,
                        "Shared supplier-offer evidence made supplier A look immediately feasible.",
                    ),
                )
                await emitter.emit(
                    WorkflowEventType.EVIDENCE_RECORDED,
                    self._evidence_payload(
                        echo,
                        "An agent-derived echo reused the same upstream assumption.",
                    ),
                    actor_id="cost-agent",
                )
            elif tool_name == "get_lead_times":
                claim = self._claim(
                    "claim.supplier_b.lead_time.stale",
                    subject="supplier-b",
                    predicate="lead_time",
                    value=3,
                    supplier_id="supplier-b",
                    human_summary="Supplier B appears too slow for day-one demand.",
                )
                evidence = evidence_from_tool_result(
                    evidence_id="evidence.supplier_b.stale_lead_time",
                    call=self._call(tool_name, access_mode=MCPAccessMode.READ),
                    result=result,
                    mapping=ToolEvidenceMapping(
                        canonical_source_type="supplier_api",
                        canonical_source_id="supplier-b-master",
                    ),
                    claim_ids=(claim.claim_id,),
                )
                self.public_claims[claim.claim_id] = claim
                self.public_evidence[evidence.evidence_id] = evidence
            elif tool_name == "get_warehouse_capacity":
                claim = self._claim(
                    "claim.warehouse.capacity",
                    subject="warehouse-north",
                    predicate="warehouse_capacity",
                    value=20,
                    warehouse_id="warehouse-north",
                    human_summary="Warehouse North has remaining cold-storage capacity.",
                )
                evidence = evidence_from_tool_result(
                    evidence_id="evidence.warehouse.capacity",
                    call=self._call(tool_name, access_mode=MCPAccessMode.READ),
                    result=result,
                    mapping=ToolEvidenceMapping(
                        canonical_source_type="warehouse_api",
                        canonical_source_id="warehouse-north-capacity",
                    ),
                    claim_ids=(claim.claim_id,),
                )
                self.public_claims[claim.claim_id] = claim
                self.public_evidence[evidence.evidence_id] = evidence
        return self._optimization_request(version="inputs-v1", preferred_supplier="supplier-a")

    async def prepare_dissent(self, *, emitter: RunEmitter) -> None:
        self.dissent_claims.clear()
        self.dissent_evidence.clear()
        await emitter.emit(
            WorkflowEventType.TASK_STARTED,
            {
                "phase": "jury",
                "cycle": 1 if self.current_phase == "initial" else 2,
                "task": "clean_room_dissent",
                "context_id": self.dissent_client.namespace.context_id,
                "memory_namespace": self.dissent_client.namespace.memory_namespace,
                "tool_cache_namespace": self.dissent_client.namespace.tool_cache_namespace,
            },
            actor_id="dissent",
        )
        result = await self.dissent_client.invoke(
            self._call("get_lead_times", access_mode=MCPAccessMode.READ)
        )
        if self.current_phase == "initial":
            claim = self._claim(
                "claim.supplier_a.lead_time.live",
                subject="supplier-a",
                predicate="lead_time",
                value=10,
                supplier_id="supplier-a",
                human_summary="Fresh Dissent retrieval shows supplier A is actually slow.",
            )
            mapping = ToolEvidenceMapping(
                canonical_source_type="partner_audit",
                canonical_source_id="supplier-a-live-audit",
            )
        else:
            claim = self._claim(
                "claim.supplier_b.lead_time.live",
                subject="supplier-b",
                predicate="lead_time",
                value=1,
                supplier_id="supplier-b",
                human_summary=(
                    "Fresh Dissent retrieval confirms supplier B can still arrive in time."
                ),
            )
            mapping = ToolEvidenceMapping(
                canonical_source_type="partner_audit",
                canonical_source_id="supplier-b-live-audit",
            )
        evidence = evidence_from_tool_result(
            evidence_id=f"evidence.dissent.{self.current_phase}",
            call=self._call("get_lead_times", access_mode=MCPAccessMode.READ),
            result=result,
            mapping=mapping,
            claim_ids=(claim.claim_id,),
            agent_id="dissent",
        )
        self.dissent_claims[claim.claim_id] = claim
        self.dissent_evidence[evidence.evidence_id] = evidence
        await emitter.emit(
            WorkflowEventType.EVIDENCE_RECORDED,
            self._evidence_payload(evidence, claim.human_summary),
            actor_id="dissent",
        )
        await emitter.emit(
            WorkflowEventType.TASK_COMPLETED,
            {
                "phase": "jury",
                "cycle": 1 if self.current_phase == "initial" else 2,
                "task": "clean_room_dissent",
                "checked_claims": [claim.claim_id],
            },
            actor_id="dissent",
        )

    async def refresh_after_investigation(self, *, emitter: RunEmitter) -> OptimizationRequest:
        self.current_phase = "replanned"
        self.public_server.supplier_offers = [
            {
                "offer_id": "offer-a-slow",
                "supplier_id": "supplier-a",
                "sku_id": "sku-apples",
                "destination_warehouse_id": "warehouse-north",
                "arrival_bucket_id": "bucket-day-2",
                "capacity": 4,
                "unit_cost": 4,
                "pack_size": 1,
                "minimum_order": 1,
                "risk": 3,
                "expected_waste_rate": 1,
            },
            {
                "offer_id": "offer-b-live",
                "supplier_id": "supplier-b",
                "sku_id": "sku-apples",
                "destination_warehouse_id": "warehouse-north",
                "arrival_bucket_id": "bucket-day-1",
                "capacity": 4,
                "unit_cost": 7,
                "pack_size": 1,
                "minimum_order": 1,
                "risk": 1,
                "expected_waste_rate": 1,
            },
        ]
        self.public_server.lead_times = [
            {"supplier_id": "supplier-a", "lead_time_days": 10},
            {"supplier_id": "supplier-b", "lead_time_days": 1},
        ]
        self.public_server.observation_version = "public-v2"
        offer_result = await self.public_client.invoke(
            self._call("get_supplier_offers", access_mode=MCPAccessMode.READ)
        )
        lead_time_result = await self.public_client.invoke(
            self._call("get_lead_times", access_mode=MCPAccessMode.READ)
        )
        lead_claim = self._claim(
            "claim.supplier_b.lead_time",
            subject="supplier-b",
            predicate="lead_time",
            value=1,
            supplier_id="supplier-b",
            human_summary="Supplier B is now the verified day-one option.",
        )
        offer_claim = self._claim(
            "claim.supplier_b.unit_price",
            subject="supplier-b",
            predicate="unit_price",
            value=7,
            supplier_id="supplier-b",
            human_summary="Supplier B is costlier but feasible with current lead time.",
        )
        evidence = evidence_from_tool_result(
            evidence_id="evidence.supplier_b.live_offer",
            call=self._call("get_supplier_offers", access_mode=MCPAccessMode.READ),
            result=offer_result,
            mapping=ToolEvidenceMapping(
                canonical_source_type="supplier_api",
                canonical_source_id="supplier-b-live-offer",
            ),
            claim_ids=(lead_claim.claim_id, offer_claim.claim_id),
        )
        audit_evidence = evidence_from_tool_result(
            evidence_id="evidence.supplier_b.live_audit",
            call=self._call("get_lead_times", access_mode=MCPAccessMode.READ),
            result=lead_time_result,
            mapping=ToolEvidenceMapping(
                canonical_source_type="partner_audit",
                canonical_source_id="supplier-b-public-audit",
            ),
            claim_ids=(lead_claim.claim_id,),
        )
        self.public_claims[lead_claim.claim_id] = lead_claim
        self.public_claims[offer_claim.claim_id] = offer_claim
        self.public_evidence[evidence.evidence_id] = evidence
        self.public_evidence[audit_evidence.evidence_id] = audit_evidence
        await emitter.emit(
            WorkflowEventType.EVIDENCE_RECORDED,
            self._evidence_payload(
                evidence,
                (
                    "Investigation refreshed the public offer set and replaced supplier A "
                    "with supplier B."
                ),
            ),
        )
        await emitter.emit(
            WorkflowEventType.EVIDENCE_RECORDED,
            self._evidence_payload(
                audit_evidence,
                "Investigation added an independent public audit for supplier B lead time.",
            ),
        )
        return self._optimization_request(version="inputs-v2", preferred_supplier="supplier-b")

    def jury_inputs(self, request: JuryRequest) -> JuryInputs:
        if self.current_phase == "initial":
            checked_claim_id = "claim.supplier_a.lead_time.live"
            contradiction_ids = (
                "contradiction:claim.supplier_a.lead_time:claim.supplier_a.lead_time.live",
            )
            checked_claims = (
                self.public_claims["claim.supplier_a.lead_time"],
                self.public_claims["claim.supplier_a.unit_price"],
                self.public_claims["claim.inventory.local"],
                self.public_claims["claim.warehouse.capacity"],
                self.dissent_claims[checked_claim_id],
            )
            evidence = tuple(self.public_evidence.values()) + tuple(self.dissent_evidence.values())
            protocol = DissentProtocol()
            report = protocol.record_plan(
                DissentInvestigationPlan(
                    context_id=self.dissent_client.namespace.context_id,
                    memory_namespace=self.dissent_client.namespace.memory_namespace,
                    tool_cache_namespace=self.dissent_client.namespace.tool_cache_namespace,
                    checks=("verify supplier-a lead time from an independent source",),
                    tool_budget=1,
                )
            )
            report = protocol.record_fresh_retrieval(
                report, evidence_ids=tuple(self.dissent_evidence.keys())
            )
            dissent = protocol.compare_with_existing_graph(
                report,
                checked_claim_ids=(checked_claim_id,),
                contradiction_ids=contradiction_ids,
                establishes_invalidity=False,
            )
            return JuryInputs(
                claims=checked_claims,
                evidence=evidence,
                dissent=dissent,
                critical_claim_ids=frozenset(
                    {"claim.supplier_a.lead_time", "claim.supplier_a.unit_price"}
                ),
                gate_facts=GateFacts(stale_execution_claim_ids=("claim.supplier_a.lead_time",)),
            )
        checked_claim_id = "claim.supplier_b.lead_time.live"
        checked_claims = (
            self.public_claims["claim.supplier_b.lead_time"],
            self.public_claims["claim.supplier_b.unit_price"],
            self.public_claims["claim.inventory.local"],
            self.public_claims["claim.warehouse.capacity"],
            self.dissent_claims[checked_claim_id],
        )
        evidence = (
            self.public_evidence["evidence.inventory.local"],
            self.public_evidence["evidence.warehouse.capacity"],
            self.public_evidence["evidence.supplier_b.live_offer"],
            self.public_evidence["evidence.supplier_b.live_audit"],
            self.dissent_evidence[f"evidence.dissent.{self.current_phase}"],
        )
        protocol = DissentProtocol()
        report = protocol.record_plan(
            DissentInvestigationPlan(
                context_id=self.dissent_client.namespace.context_id,
                memory_namespace=self.dissent_client.namespace.memory_namespace,
                tool_cache_namespace=self.dissent_client.namespace.tool_cache_namespace,
                checks=("verify supplier-b lead time from an independent source",),
                tool_budget=1,
            )
        )
        report = protocol.record_fresh_retrieval(
            report, evidence_ids=tuple(self.dissent_evidence.keys())
        )
        dissent = protocol.compare_with_existing_graph(
            report,
            checked_claim_ids=(checked_claim_id,),
        )
        return JuryInputs(
            claims=checked_claims,
            evidence=evidence,
            dissent=dissent,
            critical_claim_ids=frozenset(
                {"claim.supplier_b.lead_time", "claim.supplier_b.unit_price"}
            ),
        )

    def record_evaluation(self, evaluation: JuryEvaluation) -> None:
        self.latest_evaluation = evaluation

    def expected_revalidation_snapshot(self, approved_plan: CandidatePlan) -> RevalidationSnapshot:
        supplier_ids = {line.supplier_id for line in approved_plan.procurement}
        warehouse_ids = {line.destination_warehouse_id for line in approved_plan.procurement}
        lot_ids = {lot_id for line in approved_plan.distribution for lot_id in line.source_lot_ids}
        lead_time_days = {
            "supplier-b": 1,
            "supplier-a": 10,
        }
        capacity = {"warehouse-north": 20}
        offer_ids = (
            {"offer-a-slow", "offer-b-live"}
            if self.current_phase != "initial"
            else {
                "offer-a-stale",
                "offer-b-slow",
            }
        )
        return RevalidationSnapshot(
            inventory_lot_ids=frozenset(lot_ids or {"lot-local-1"}),
            offer_ids=frozenset(offer_ids),
            lead_time_days={
                key: value for key, value in lead_time_days.items() if key in supplier_ids
            },
            warehouse_capacity_units={
                key: value for key, value in capacity.items() if key in warehouse_ids
            },
        )

    def build_execution_request(self, approved_plan: CandidatePlan) -> ExecutionRequest:
        return ExecutionRequest(
            execution_id=self.ids.new_id("execution"),
            planning_run_id=self.run_id,
            approved_plan_id=approved_plan.plan_id,
            jury_evaluation_id=(
                self.latest_evaluation.evaluation_id if self.latest_evaluation else "pending"
            ),
            idempotency_key=f"{self.run_id}:{approved_plan.plan_id}",
            approval_policy_version="decision-integrity-v1",
            requested_at=self.clock.now(),
            action={
                "procurement_line_count": len(approved_plan.procurement),
                "distribution_line_count": len(approved_plan.distribution),
            },
        )

    def _call(self, tool_name: str, *, access_mode: MCPAccessMode) -> MCPToolCall:
        return MCPToolCall(
            call_id=self.ids.new_id("mcp"),
            server_name=(
                "mock-procurement" if access_mode is MCPAccessMode.WRITE else "mock-observation"
            ),
            tool_name=tool_name,
            arguments={},
            access_mode=access_mode,
            idempotency_key=f"{self.run_id}:{tool_name}",
        )

    def _claim(
        self,
        claim_id: str,
        *,
        subject: str,
        predicate: str,
        value: int,
        human_summary: str,
        supplier_id: str | None = None,
        warehouse_id: str | None = None,
    ) -> TypedClaim:
        return TypedClaim(
            claim_id=claim_id,
            subject=subject,
            predicate=predicate,
            value=value,
            unit="unit",
            valid_at=self.clock.now(),
            scope=ClaimScope(
                organization_id="org-civitas",
                sku_id="sku-apples",
                supplier_id=supplier_id,
                warehouse_id=warehouse_id,
            ),
            human_summary=human_summary,
        )

    def _optimization_request(
        self, *, version: str, preferred_supplier: str
    ) -> OptimizationRequest:
        annotations = {
            f"{self.run_id}-balanced-01": self._plan_annotation(preferred_supplier),
            f"{self.run_id}-cost-01": self._plan_annotation(preferred_supplier),
            f"{self.run_id}-waste-01": self._plan_annotation(preferred_supplier),
            f"{self.run_id}-risk-01": self._plan_annotation(preferred_supplier),
            f"{self.run_id}-redistribution-01": self._plan_annotation(preferred_supplier),
            f"{self.run_id}-holding-01": self._plan_annotation(preferred_supplier),
            f"{self.run_id}-concentration-01": self._plan_annotation(preferred_supplier),
        }
        return OptimizationRequest(
            planning_run_id=self.run_id,
            input_data_version=version,
            objectives_version="objectives-v1",
            maximum_alternatives=5,
            constraints={
                "base_unit": "each",
                "buckets": [
                    {
                        "bucket_id": "bucket-day-1",
                        "start": "2026-08-27T00:00:00+00:00",
                        "end": "2026-08-28T00:00:00+00:00",
                        "urgency": 3,
                    },
                    {
                        "bucket_id": "bucket-day-2",
                        "start": "2026-08-28T00:00:00+00:00",
                        "end": "2026-08-29T00:00:00+00:00",
                        "urgency": 1,
                    },
                ],
                "demands": self.public_server.demand,
                "inventory_lots": [
                    {
                        "lot_id": "lot-local-1",
                        "sku_id": "sku-apples",
                        "warehouse_id": "warehouse-north",
                        "quantity": 2,
                        "expires_at": "2026-08-31T00:00:00+00:00",
                        "status": "available",
                        "unit_cost": 0,
                    }
                ],
                "supplier_offers": self.public_server.supplier_offers,
                "warehouse_capacities": [
                    {
                        "warehouse_id": "warehouse-north",
                        "bucket_id": "bucket-day-1",
                        "maximum_base_units": 20,
                    },
                    {
                        "warehouse_id": "warehouse-north",
                        "bucket_id": "bucket-day-2",
                        "maximum_base_units": 20,
                    },
                ],
                "plan_annotations": annotations,
            },
        )

    def _plan_annotation(self, preferred_supplier: str) -> dict[str, list[str]]:
        if preferred_supplier == "supplier-a":
            return {
                "claim_ids": ["claim.supplier_a.lead_time", "claim.supplier_a.unit_price"],
                "evidence_ids": [
                    "evidence.supplier_a.stale_offer",
                    "evidence.supplier_a.echo",
                ],
            }
        return {
            "claim_ids": ["claim.supplier_b.lead_time", "claim.supplier_b.unit_price"],
            "evidence_ids": [
                "evidence.supplier_b.live_offer",
                "evidence.supplier_b.live_audit",
            ],
        }

    @staticmethod
    def _evidence_payload(evidence: EvidenceRecord, note: str) -> dict[str, Any]:
        return {
            "phase": "evidence",
            "evidence_id": evidence.evidence_id,
            "claim_ids": list(evidence.claim_ids),
            "origin": evidence.origin.value,
            "source_group": (
                f"{evidence.identity.canonical_source_type}:{evidence.identity.canonical_source_id}"
            ),
            "summary": evidence.content_summary,
            "note": note,
        }


class DemoIntegrationService:
    MAX_RETAINED_RUNS = 100

    def __init__(self) -> None:
        self._clock = SystemClock()
        self._ids = UUIDIDs()
        self._runs: dict[str, DemoRunRecord] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._scenarios = (
            DemoScenarioSummary(
                scenario_id="false-consensus-demo",
                title="False consensus with clean-room dissent",
                description=(
                    "Supplier A initially wins on shared stale evidence, Dissent finds the "
                    "contradiction, the plan is reopened, supplier B is approved, then "
                    "execution is revalidated and "
                    "duplicate-protected."
                ),
            ),
        )

    def list_scenarios(self) -> tuple[DemoScenarioSummary, ...]:
        return self._scenarios

    def get_run(self, run_id: str) -> DemoRunRecord | None:
        return self._runs.get(run_id)

    async def create_run(self, scenario_id: str) -> DemoRunRecord:
        run = self._create_run_record(scenario_id)
        await self._execute_run(run)
        return run

    def start_run(self, scenario_id: str) -> DemoRunRecord:
        run = self._create_run_record(scenario_id)
        task = asyncio.create_task(self._execute_run(run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    def _create_run_record(self, scenario_id: str) -> DemoRunRecord:
        scenario = next((item for item in self._scenarios if item.scenario_id == scenario_id), None)
        if scenario is None:
            raise KeyError(scenario_id)
        self._prune_completed_runs()
        if len(self._runs) >= self.MAX_RETAINED_RUNS:
            raise RuntimeError("Too many demo runs are currently active; retry later.")
        run_id = self._ids.new_id("planning-run")
        run = DemoRunRecord(
            run_id=run_id,
            scenario_id=scenario.scenario_id,
            title=scenario.title,
            started_at=self._clock.now(),
            status="running",
        )
        self._runs[run_id] = run
        return run

    def _prune_completed_runs(self) -> None:
        completed = sorted(
            (run for run in self._runs.values() if run._done.is_set()),
            key=lambda run: run.started_at,
        )
        while completed and len(self._runs) >= self.MAX_RETAINED_RUNS:
            expired = completed.pop(0)
            self._runs.pop(expired.run_id, None)

    async def _execute_run(self, run: DemoRunRecord) -> None:
        emitter = RunEmitter(run, self._ids, self._clock)
        scenario = FalseConsensusScenarioState(run_id=run.run_id, ids=self._ids, clock=self._clock)
        final_status = "failed"
        try:
            request = await scenario.retrieve_initial_inputs(emitter=emitter)
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
            checkpoint = workflow.start(planning_run_id=run.run_id, optimization_request=request)
            limits = WorkflowLimits(
                max_cycles=3,
                deadline_at=self._clock.now() + timedelta(minutes=5),
            )
            while not checkpoint.completed:
                checkpoint, events = await workflow.advance(checkpoint, limits=limits)
                for event in events:
                    await emitter.emit_existing(event)
                if checkpoint.phase == WorkflowPhase.INVESTIGATION and not checkpoint.completed:
                    run.status = "investigating"
                    refreshed_request = await scenario.refresh_after_investigation(emitter=emitter)
                    checkpoint = checkpoint.model_copy(
                        update={"optimization_request": refreshed_request}
                    )
                if checkpoint.completed:
                    break

            run.final_state = checkpoint.final_state
            if checkpoint.final_state != JuryState.APPROVE.value:
                return
            if checkpoint.optimization_result is None or checkpoint.parliament is None:
                raise RuntimeError("approved workflow is missing its selected solver result")

            selected_plan = next(
                plan
                for plan in checkpoint.optimization_result.alternatives
                if plan.plan_id == checkpoint.parliament.selected_plan_id
            )
            expected_snapshot = scenario.expected_revalidation_snapshot(selected_plan)
            execution_request = scenario.build_execution_request(selected_plan)
            first_result = await scenario.execution.execute(
                execution_request,
                approved_plan=selected_plan,
                expected_snapshot=expected_snapshot,
            )
            await emitter.emit(
                WorkflowEventType.EXECUTION_UPDATED,
                self._execution_payload(checkpoint, first_result),
            )
            duplicate_result = await scenario.execution.execute(
                execution_request,
                approved_plan=selected_plan,
                expected_snapshot=expected_snapshot,
            )
            await emitter.emit(
                WorkflowEventType.EXECUTION_UPDATED,
                self._execution_payload(checkpoint, duplicate_result),
            )
            final_status = "completed"
        except Exception as exc:
            await emitter.emit(
                WorkflowEventType.RUN_FAILED,
                {"phase": "terminal", "reason": str(exc)},
            )
        finally:
            await run.close()
            run.status = final_status

    @staticmethod
    def _execution_payload(
        checkpoint: WorkflowCheckpoint, result: ExecutionResult
    ) -> dict[str, Any]:
        return {
            "phase": checkpoint.phase.value,
            "cycle": checkpoint.cycle,
            "state": result.state.value,
            "detail": result.detail,
            "external_references": list(result.external_references),
            "approved_plan_id": (
                checkpoint.parliament.selected_plan_id if checkpoint.parliament else None
            ),
        }


def _sse_frame(payload: SSEPayload) -> str:
    return (
        f"id: {payload.id}\n"
        f"event: {payload.event.value}\n"
        f"data: {payload.data.model_dump_json()}\n\n"
    )


def create_app() -> FastAPI:
    service = DemoIntegrationService()
    app = FastAPI(title="Civitas API", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/demo-scenarios")
    async def demo_scenarios() -> dict[str, object]:
        return {
            "scenarios": [
                {
                    "scenario_id": item.scenario_id,
                    "title": item.title,
                    "description": item.description,
                }
                for item in service.list_scenarios()
            ]
        }

    @app.post("/api/demo-runs")
    async def create_demo_run(payload: CreateDemoRunRequest) -> dict[str, object]:
        try:
            run = service.start_run(payload.scenario_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"Unknown scenario {payload.scenario_id!r}."
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return {
            "run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "status": run.status,
            "stream_url": f"/api/demo-runs/{run.run_id}/events",
        }

    @app.get("/api/demo-runs/{run_id}")
    async def get_demo_run(run_id: str) -> dict[str, object]:
        run = service.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        latest = run.events[-1] if run.events else None
        latest_payload = latest.payload if latest is not None else None
        return {
            "run_id": run.run_id,
            "scenario_id": run.scenario_id,
            "title": run.title,
            "status": run.status,
            "final_state": run.final_state,
            "event_count": len(run.events),
            "current_cycle": run.current_cycle,
            "latest_event_type": latest.event_type.value if latest else None,
            "latest_payload": latest_payload,
            "events": [event.model_dump(mode="json") for event in run.events],
        }

    @app.get("/api/demo-runs/{run_id}/events")
    async def stream_demo_run(run_id: str) -> StreamingResponse:
        run = service.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")

        async def iterator() -> AsyncIterator[str]:
            async for event in run.stream():
                payload = SSEPayload(id=str(event.sequence), event=event.event_type, data=event)
                yield _sse_frame(payload)

        return StreamingResponse(iterator(), media_type="text/event-stream")

    return app
