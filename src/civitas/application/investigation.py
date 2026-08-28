"""Durable, provider-backed investigation and clean-room Jury services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from civitas.contracts.claims import ClaimScope, TypedClaim
from civitas.contracts.common import JsonObject
from civitas.contracts.evidence import EvidenceRecord
from civitas.contracts.jury import JuryEvaluation, JuryRequest
from civitas.contracts.optimization import OptimizationRequest
from civitas.contracts.providers import OperationalObservation, ProviderEvidenceRead
from civitas.contracts.tools import MCPAccessMode, MCPToolCall
from civitas.evidence import (
    DissentInvestigationPlan,
    DissentProtocol,
    DissentReport,
    JuryEvaluator,
    JuryInputs,
)
from civitas.evidence.contradictions import detect_contradictions
from civitas.integrations.mcp import CleanRoomNamespace
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator
from civitas.workflow.models import InvestigationOutcome, WorkflowCheckpoint, WorkflowLimits


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    claims: tuple[TypedClaim, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()


class EvidenceReader(Protocol):
    async def read(
        self,
        *,
        call: MCPToolCall,
        evidence_id: str,
        claim_ids: Sequence[str] = (),
        agent_id: str | None = None,
    ) -> ProviderEvidenceRead: ...


class DurableEvidenceLedger(Protocol):
    async def load(
        self,
        *,
        planning_run_id: str,
        claim_ids: Sequence[str] = (),
        evidence_ids: Sequence[str] = (),
    ) -> EvidenceSnapshot: ...

    async def persist_read(
        self,
        *,
        planning_run_id: str,
        read: ProviderEvidenceRead,
        claims: Sequence[TypedClaim],
    ) -> EvidenceRecord: ...

    async def record_dissent(
        self,
        *,
        planning_run_id: str,
        cycle_key: str,
        phase: str,
        payload: JsonObject,
    ) -> None: ...


_PREDICATE_TO_TOOL = {
    "inventory_balance": "get_inventory",
    "inventory_reservation": "get_inventory",
    "demand_forecast": "get_demand",
    "unit_price": "get_supplier_offers",
    "supplier_availability": "get_supplier_offers",
    "supplier_capacity": "get_supplier_offers",
    "lead_time": "get_lead_times",
    "delivery_window": "get_lead_times",
    "warehouse_capacity": "get_warehouse_capacity",
    "transport_capacity": "get_transport_capacity",
}


class JuryDirectedInvestigator:
    """Turns Jury feedback into bounded reads and a solver-owned replan request."""

    def __init__(
        self,
        *,
        reader: EvidenceReader,
        ledger: DurableEvidenceLedger,
        ids: IDGenerator,
        server_name: str,
        organization_id: str,
    ) -> None:
        self._reader = reader
        self._ledger = ledger
        self._ids = ids
        self._server_name = server_name
        self._organization_id = organization_id

    async def investigate(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        limits: WorkflowLimits,
    ) -> InvestigationOutcome:
        snapshot = await self._ledger.load(planning_run_id=checkpoint.planning_run_id)
        tools = _tools_for_feedback(checkpoint.investigation_backlog, snapshot.claims)
        remaining = _remaining_tool_budget(checkpoint, limits)
        selected = tools if remaining is None else tools[:remaining]
        unavailable = tools[len(selected) :]
        reads: list[ProviderEvidenceRead] = []
        claims: list[TypedClaim] = []
        for tool_name in selected:
            try:
                read, observed_claims = await self._retrieve(
                    checkpoint.planning_run_id,
                    checkpoint.cycle,
                    tool_name,
                    agent_id="planner-investigation",
                )
            except Exception as error:
                unavailable = (
                    *unavailable,
                    f"provider_error:{tool_name}:{type(error).__name__}",
                )
                continue
            persisted = await self._ledger.persist_read(
                planning_run_id=checkpoint.planning_run_id,
                read=read,
                claims=observed_claims,
            )
            read = read.model_copy(update={"evidence": persisted})
            reads.append(read)
            claims.extend(observed_claims)
        updated_request = _replanning_request(
            checkpoint.optimization_request,
            reads=reads,
            claims=claims,
        )
        return InvestigationOutcome(
            optimization_request=updated_request,
            completed_task_ids=tuple(
                f"cycle-{checkpoint.cycle}:{read.call.tool_name}" for read in reads
            ),
            unavailable_tasks=tuple(f"tool_budget:{tool}" for tool in unavailable),
            evidence_ids=tuple(read.evidence.evidence_id for read in reads),
            evidence_fingerprints=tuple(
                read.evidence.identity.raw_response_sha256 for read in reads
            ),
            canonical_source_groups=tuple(
                dict.fromkeys(
                    f"{read.evidence.identity.canonical_source_type}:"
                    f"{read.evidence.identity.canonical_source_id}"
                    for read in reads
                )
            ),
            tool_calls_used=len(reads),
        )

    async def _retrieve(
        self,
        planning_run_id: str,
        cycle: int,
        tool_name: str,
        *,
        agent_id: str,
    ) -> tuple[ProviderEvidenceRead, tuple[TypedClaim, ...]]:
        call = MCPToolCall(
            call_id=self._ids.new_id("mcp-read"),
            server_name=self._server_name,
            tool_name=tool_name,
            arguments={
                "organization_id": self._organization_id,
                "planning_run_id": planning_run_id,
            },
            access_mode=MCPAccessMode.READ,
        )
        evidence_id = self._ids.new_id("evidence")
        read = await self._reader.read(
            call=call,
            evidence_id=evidence_id,
            agent_id=agent_id,
        )
        claims = tuple(
            _claim_from_observation(
                observation,
                claim_id=self._ids.new_id("claim"),
                organization_id=self._organization_id,
            )
            for observation in read.observations
        )
        evidence = read.evidence.model_copy(
            update={"claim_ids": tuple(claim.claim_id for claim in claims)}
        )
        return read.model_copy(update={"evidence": evidence}), claims


class DurableCleanRoomJury:
    """Jury port that records its plan before isolated, fresh provider retrieval."""

    def __init__(
        self,
        *,
        dissent_reader: EvidenceReader,
        ledger: DurableEvidenceLedger,
        ids: IDGenerator,
        clock: Clock,
        server_name: str,
        organization_id: str,
        tool_budget: int = 3,
        evaluator: JuryEvaluator | None = None,
        clean_room_namespace: CleanRoomNamespace | None = None,
    ) -> None:
        if tool_budget < 1:
            raise ValueError("Dissent tool budget must be positive")
        self._reader = dissent_reader
        self._ledger = ledger
        self._ids = ids
        self._clock = clock
        self._server_name = server_name
        self._organization_id = organization_id
        self._tool_budget = tool_budget
        self._evaluator = evaluator or JuryEvaluator()
        self._clean_room_namespace = clean_room_namespace

    async def evaluate(self, request: JuryRequest) -> JuryEvaluation:
        baseline = await self._ledger.load(
            planning_run_id=request.planning_run_id,
            claim_ids=request.supporting_claim_ids,
            evidence_ids=request.evidence_ids,
        )
        tools = _tools_for_claims(baseline.claims)
        selected_tools = tools[: self._tool_budget]
        unavailable = tools[self._tool_budget :]
        cycle_key = self._ids.new_id("dissent")
        namespace = self._clean_room_namespace
        plan = DissentInvestigationPlan(
            context_id=namespace.context_id if namespace else f"{cycle_key}-context",
            memory_namespace=(namespace.memory_namespace if namespace else f"{cycle_key}-memory"),
            tool_cache_namespace=(
                namespace.tool_cache_namespace if namespace else f"{cycle_key}-cache"
            ),
            checks=tuple(f"fresh:{tool}" for tool in tools),
            tool_budget=self._tool_budget,
        )
        report = DissentProtocol.record_plan(plan)
        await self._ledger.record_dissent(
            planning_run_id=request.planning_run_id,
            cycle_key=cycle_key,
            phase=report.phase.value,
            payload=_report_payload(report),
        )
        fresh_reads: list[ProviderEvidenceRead] = []
        fresh_claims: list[TypedClaim] = []
        investigator = JuryDirectedInvestigator(
            reader=self._reader,
            ledger=self._ledger,
            ids=self._ids,
            server_name=self._server_name,
            organization_id=self._organization_id,
        )
        for tool_name in selected_tools:
            try:
                read, claims = await investigator._retrieve(
                    request.planning_run_id,
                    cycle=0,
                    tool_name=tool_name,
                    agent_id="dissent",
                )
            except Exception as error:
                unavailable = (
                    *unavailable,
                    f"fresh:{tool_name}:{type(error).__name__}",
                )
                continue
            persisted = await self._ledger.persist_read(
                planning_run_id=request.planning_run_id,
                read=read,
                claims=claims,
            )
            read = read.model_copy(update={"evidence": persisted})
            fresh_reads.append(read)
            fresh_claims.extend(claims)
        report = DissentProtocol.record_fresh_retrieval(
            report,
            evidence_ids=tuple(read.evidence.evidence_id for read in fresh_reads),
            unavailable_checks=tuple(f"fresh:{tool}" for tool in unavailable),
        )
        await self._ledger.record_dissent(
            planning_run_id=request.planning_run_id,
            cycle_key=cycle_key,
            phase=report.phase.value,
            payload=_report_payload(report),
        )
        combined_claims = (*baseline.claims, *fresh_claims)
        contradictions = detect_contradictions(
            combined_claims,
            critical_claim_ids=frozenset(request.supporting_claim_ids),
        )
        report = DissentProtocol.compare_with_existing_graph(
            report,
            checked_claim_ids=tuple(claim.claim_id for claim in fresh_claims),
            contradiction_ids=tuple(item.contradiction_id for item in contradictions),
            # Contradictions route to INVESTIGATE. Only an independent deterministic
            # verifier may establish proposal invalidity and trigger REJECT.
            establishes_invalidity=False,
        )
        await self._ledger.record_dissent(
            planning_run_id=request.planning_run_id,
            cycle_key=cycle_key,
            phase=report.phase.value,
            payload=_report_payload(report),
        )
        expanded_request = request.model_copy(
            update={
                "supporting_claim_ids": tuple(
                    dict.fromkeys(
                        (*request.supporting_claim_ids, *(claim.claim_id for claim in fresh_claims))
                    )
                ),
                "evidence_ids": tuple(
                    dict.fromkeys(
                        (
                            *request.evidence_ids,
                            *(read.evidence.evidence_id for read in fresh_reads),
                        )
                    )
                ),
            }
        )
        return self._evaluator.evaluate(
            expanded_request,
            JuryInputs(
                claims=tuple(combined_claims),
                evidence=tuple((*baseline.evidence, *(read.evidence for read in fresh_reads))),
                dissent=report,
                critical_claim_ids=frozenset(expanded_request.supporting_claim_ids),
            ),
            evaluation_id=self._ids.new_id("jury-eval"),
            calculated_at=self._clock.now(),
        )


def _remaining_tool_budget(checkpoint: WorkflowCheckpoint, limits: WorkflowLimits) -> int | None:
    if limits.max_tool_calls == 0:
        return None
    return max(0, limits.max_tool_calls - checkpoint.tool_calls_used)


def _tools_for_claims(claims: Sequence[TypedClaim]) -> tuple[str, ...]:
    tools = tuple(
        dict.fromkeys(
            tool
            for claim in claims
            if (tool := _PREDICATE_TO_TOOL.get(claim.predicate)) is not None
        )
    )
    return tools or ("get_supplier_offers", "get_lead_times")


def _tools_for_feedback(feedback: Sequence[str], claims: Sequence[TypedClaim]) -> tuple[str, ...]:
    tools: list[str] = []
    for item in feedback:
        normalized = item.casefold()
        if "lead time" in normalized:
            tools.append("get_lead_times")
        elif "transport" in normalized:
            tools.append("get_transport_capacity")
        elif "warehouse" in normalized or "capacity" in normalized:
            tools.append("get_warehouse_capacity")
        elif "inventory" in normalized:
            tools.append("get_inventory")
        elif "demand" in normalized:
            tools.append("get_demand")
        elif any(word in normalized for word in ("price", "offer", "supplier")):
            tools.append("get_supplier_offers")
    if any("dissent" in item.casefold() or "independent" in item.casefold() for item in feedback):
        tools.extend(_tools_for_claims(claims))
    return tuple(dict.fromkeys(tools)) or _tools_for_claims(claims)


def _claim_from_observation(
    observation: OperationalObservation,
    *,
    claim_id: str,
    organization_id: str,
) -> TypedClaim:
    scope = observation.scope
    raw_value = observation.value
    value: int | float | str | bool
    if isinstance(raw_value, Decimal):
        value = int(raw_value) if raw_value == raw_value.to_integral_value() else float(raw_value)
    else:
        value = raw_value
    return TypedClaim(
        claim_id=claim_id,
        subject=observation.subject,
        predicate=observation.predicate,
        value=value,
        unit=observation.unit,
        valid_at=observation.valid_at,
        scope=ClaimScope(
            organization_id=organization_id,
            sku_id=_scope_text(scope, "sku_id"),
            warehouse_id=_scope_text(scope, "warehouse_id"),
            supplier_id=_scope_text(scope, "supplier_id"),
        ),
        human_summary=(
            f"Fresh provider observation: {observation.subject} "
            f"{observation.predicate}={observation.value} {observation.unit}."
        ),
    )


def _scope_text(scope: JsonObject, key: str) -> str | None:
    value = scope.get(key)
    return value if isinstance(value, str) and value else None


def _replanning_request(
    request: OptimizationRequest,
    *,
    reads: Sequence[ProviderEvidenceRead],
    claims: Sequence[TypedClaim],
) -> OptimizationRequest:
    constraints = dict(request.constraints)
    annotations = constraints.get("plan_annotations")
    if isinstance(annotations, dict):
        revised: JsonObject = {}
        claim_ids = [claim.claim_id for claim in claims]
        evidence_ids = [read.evidence.evidence_id for read in reads]
        for plan_id, raw in annotations.items():
            if not isinstance(plan_id, str) or not isinstance(raw, dict):
                continue
            existing_claim_ids = _string_list(raw.get("claim_ids"))
            existing_evidence_ids = _string_list(raw.get("evidence_ids"))
            revised[plan_id] = {
                **raw,
                "claim_ids": list(dict.fromkeys((*existing_claim_ids, *claim_ids))),
                "evidence_ids": list(dict.fromkeys((*existing_evidence_ids, *evidence_ids))),
            }
        constraints["plan_annotations"] = revised
    constraints["investigation_observations"] = [
        observation.model_dump(mode="json") for read in reads for observation in read.observations
    ]
    for read in reads:
        if read.call.tool_name == "get_supplier_offers":
            offers = read.result.payload.get("offers")
            if (
                isinstance(offers, list)
                and offers
                and all(_is_solver_offer(item) for item in offers)
            ):
                constraints["supplier_offers"] = offers
    fingerprint = hashlib.sha256(
        json.dumps(
            [read.evidence.identity.raw_response_sha256 for read in reads],
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return request.model_copy(
        update={
            "input_data_version": f"{request.input_data_version}+investigation-{fingerprint}",
            "constraints": constraints,
        }
    )


def _is_solver_offer(value: object) -> bool:
    return isinstance(value, dict) and {
        "offer_id",
        "supplier_id",
        "sku_id",
        "destination_warehouse_id",
        "arrival_bucket_id",
        "capacity",
        "unit_cost",
    }.issubset(value)


def _string_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _report_payload(report: DissentReport) -> JsonObject:
    plan = report.plan
    return {
        "context_id": plan.context_id,
        "memory_namespace": plan.memory_namespace,
        "tool_cache_namespace": plan.tool_cache_namespace,
        "checks": list(plan.checks),
        "tool_budget": plan.tool_budget,
        "read_only": plan.read_only,
        "fresh_evidence_ids": list(report.fresh_evidence_ids),
        "checked_claim_ids": list(report.checked_claim_ids),
        "unavailable_checks": list(report.unavailable_checks),
        "contradiction_ids": list(report.contradiction_ids),
        "establishes_invalidity": report.establishes_invalidity,
        "failure_reason": report.failure_reason,
    }
