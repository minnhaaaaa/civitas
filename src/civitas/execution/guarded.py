"""Guarded execution with freshness checks, reservations, and idempotent writes."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civitas.contracts.claims import ClaimScope, TypedClaim
from civitas.contracts.common import Contract, Quantity
from civitas.contracts.enums import ExecutionState, FeasibilityStatus, JuryState
from civitas.contracts.evidence import EvidenceRecord
from civitas.contracts.execution import ExecutionRequest, ExecutionResult
from civitas.contracts.jury import JuryRequest
from civitas.contracts.optimization import CandidatePlan, DistributionLine, ProcurementLine
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.evidence.dissent import DissentInvestigationPlan, DissentProtocol
from civitas.evidence.jury import GateFacts, IntegrityPolicyV1, JuryEvaluator, JuryInputs
from civitas.integrations.mcp import evidence_from_tool_result
from civitas.persistence.inventory import (
    DuplicateReservation,
    InsufficientInventory,
    InventoryService,
)
from civitas.persistence.models import (
    CandidatePlanModel,
    DistributionLineModel,
    ExecutionAuditModel,
    InventoryLotModel,
    JuryDecisionModel,
    PlanningRunModel,
    ProcurementLineModel,
    SKUModel,
    SupplierModel,
    WarehouseModel,
)
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator
from civitas.ports.mcp import MCPPort

REQUIRED_JURY_GATES = frozenset(
    {
        "solver_feasibility",
        "hard_constraints",
        "critical_contradictions",
        "critical_external_support",
        "execution_freshness",
        "autonomy_bounds",
        "human_approval",
        "proposal_validity",
        "dissent_completion",
    }
)


class GuardedExecutionOutcome(Contract):
    result: ExecutionResult
    decision: str
    reasons: tuple[str, ...] = ()
    required_investigation: tuple[str, ...] = ()


class RefreshInputsPort(Protocol):
    async def refresh(
        self,
        *,
        request: ExecutionRequest,
        plan: CandidatePlan,
        organization_id: str,
    ) -> RefreshBundle: ...


@dataclass(frozen=True, slots=True)
class RefreshBundle:
    claims: tuple[TypedClaim, ...]
    evidence: tuple[EvidenceRecord, ...]
    observed_unit_prices: Mapping[str, Decimal]
    constraints: Mapping[str, Decimal]


class ConcurrentExecutionRefresher:
    def __init__(
        self,
        *,
        mcp: MCPPort,
        ids: IDGenerator,
        server_name: str = "mock-procurement",
    ) -> None:
        self._mcp = mcp
        self._ids = ids
        self._server_name = server_name

    async def refresh(
        self,
        *,
        request: ExecutionRequest,
        plan: CandidatePlan,
        organization_id: str,
    ) -> RefreshBundle:
        del plan
        calls = tuple(
            MCPToolCall(
                call_id=self._ids.new_id("mcp"),
                server_name=self._server_name,
                tool_name=tool_name,
                arguments={"planning_run_id": request.planning_run_id},
                access_mode=MCPAccessMode.READ,
            )
            for tool_name in (
                "get_inventory",
                "get_supplier_offers",
                "get_lead_times",
                "get_warehouse_capacity",
                "get_transport_capacity",
            )
        )
        results = await asyncio.gather(*(self._mcp.invoke(call) for call in calls))
        claims: list[TypedClaim] = []
        evidence: list[EvidenceRecord] = []
        observed_unit_prices: dict[str, Decimal] = {}
        constraints: dict[str, Decimal] = {}
        for call, result in zip(calls, results, strict=True):
            parsed_claims, parsed_prices, parsed_constraints = self._parse(
                call, result, organization_id
            )
            claims.extend(parsed_claims)
            observed_unit_prices.update(parsed_prices)
            constraints.update(parsed_constraints)
            evidence.append(
                evidence_from_tool_result(
                    evidence_id=self._ids.new_id("evidence"),
                    call=call,
                    result=result,
                    claim_ids=tuple(claim.claim_id for claim in parsed_claims),
                    agent_id="execution_refresh",
                )
            )
        return RefreshBundle(
            claims=tuple(claims),
            evidence=tuple(evidence),
            observed_unit_prices=observed_unit_prices,
            constraints=constraints,
        )

    def _parse(
        self,
        call: MCPToolCall,
        result: MCPToolResult,
        organization_id: str,
    ) -> tuple[list[TypedClaim], dict[str, Decimal], dict[str, Decimal]]:
        claims: list[TypedClaim] = []
        unit_prices: dict[str, Decimal] = {}
        constraints: dict[str, Decimal] = {}
        if call.tool_name == "get_inventory":
            for lot in _records(result.payload, "lots"):
                lot_id = str(lot["lot_id"])
                claims.append(
                    TypedClaim(
                        claim_id=self._ids.new_id("claim"),
                        subject=lot_id,
                        predicate="inventory_balance",
                        value=float(lot.get("available_quantity", 0)),
                        unit=str(lot.get("unit_of_measure", "unit")),
                        valid_at=result.observed_at,
                        scope=ClaimScope(
                            organization_id=organization_id,
                            sku_id=_optional_str(lot, "sku_id"),
                            warehouse_id=_optional_str(lot, "warehouse_id"),
                        ),
                        human_summary=f"Lot {lot_id} available quantity.",
                    )
                )
        elif call.tool_name == "get_supplier_offers":
            for offer in _records(result.payload, "offers"):
                key = _offer_key(offer)
                unit_prices[key] = Decimal(str(offer.get("unit_price", "0")))
                constraints[f"offer:{key}:available"] = Decimal(
                    str(offer.get("available_quantity", "0"))
                )
                claims.extend(
                    (
                        TypedClaim(
                            claim_id=self._ids.new_id("claim"),
                            subject=key,
                            predicate="unit_price",
                            value=float(offer.get("unit_price", 0)),
                            unit="currency",
                            valid_at=result.observed_at,
                            scope=ClaimScope(
                                organization_id=organization_id,
                                sku_id=_optional_str(offer, "sku_id"),
                                warehouse_id=_optional_str(offer, "destination_warehouse_id"),
                                supplier_id=_optional_str(offer, "supplier_id"),
                            ),
                            human_summary=f"Unit price for {key}.",
                        ),
                        TypedClaim(
                            claim_id=self._ids.new_id("claim"),
                            subject=key,
                            predicate="supplier_capacity",
                            value=float(offer.get("available_quantity", 0)),
                            unit=str(offer.get("unit_of_measure", "unit")),
                            valid_at=result.observed_at,
                            scope=ClaimScope(
                                organization_id=organization_id,
                                sku_id=_optional_str(offer, "sku_id"),
                                warehouse_id=_optional_str(offer, "destination_warehouse_id"),
                                supplier_id=_optional_str(offer, "supplier_id"),
                            ),
                            human_summary=f"Available supplier quantity for {key}.",
                        ),
                    )
                )
        elif call.tool_name == "get_lead_times":
            for record in _records(result.payload, "records"):
                key = _offer_key(record)
                constraints[f"lead:{key}:days"] = Decimal(str(record.get("lead_time_days", "0")))
                claims.append(
                    TypedClaim(
                        claim_id=self._ids.new_id("claim"),
                        subject=key,
                        predicate="lead_time",
                        value=float(record.get("lead_time_days", 0)),
                        unit="day",
                        valid_at=result.observed_at,
                        scope=ClaimScope(
                            organization_id=organization_id,
                            sku_id=_optional_str(record, "sku_id"),
                            warehouse_id=_optional_str(record, "destination_warehouse_id"),
                            supplier_id=_optional_str(record, "supplier_id"),
                        ),
                        human_summary=f"Lead time for {key}.",
                    )
                )
        elif call.tool_name == "get_warehouse_capacity":
            for record in _records(result.payload, "records"):
                key = f"{record.get('warehouse_id')}:{record.get('sku_id')}"
                constraints[f"warehouse:{key}"] = Decimal(
                    str(record.get("available_quantity", "0"))
                )
                claims.append(
                    TypedClaim(
                        claim_id=self._ids.new_id("claim"),
                        subject=key,
                        predicate="warehouse_capacity",
                        value=float(record.get("available_quantity", 0)),
                        unit=str(record.get("unit_of_measure", "unit")),
                        valid_at=result.observed_at,
                        scope=ClaimScope(
                            organization_id=organization_id,
                            sku_id=_optional_str(record, "sku_id"),
                            warehouse_id=_optional_str(record, "warehouse_id"),
                        ),
                        human_summary=f"Warehouse capacity for {key}.",
                    )
                )
        elif call.tool_name == "get_transport_capacity":
            for record in _records(result.payload, "records"):
                key = _transport_key(record)
                constraints[f"transport:{key}"] = Decimal(
                    str(record.get("available_quantity", "0"))
                )
                claims.append(
                    TypedClaim(
                        claim_id=self._ids.new_id("claim"),
                        subject=key,
                        predicate="transport_capacity",
                        value=float(record.get("available_quantity", 0)),
                        unit=str(record.get("unit_of_measure", "unit")),
                        valid_at=result.observed_at,
                        scope=ClaimScope(
                            organization_id=organization_id,
                            sku_id=_optional_str(record, "sku_id"),
                            warehouse_id=_optional_str(record, "source_warehouse_id"),
                        ),
                        human_summary=f"Transport capacity for {key}.",
                    )
                )
        return claims, unit_prices, constraints


class GuardedExecutionService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        mcp: MCPPort,
        ids: IDGenerator,
        clock: Clock,
        refresher: RefreshInputsPort | None = None,
        server_name: str = "mock-procurement",
        integrity_policy: IntegrityPolicyV1 | None = None,
    ) -> None:
        self._sessions = sessions
        self._mcp = mcp
        self._ids = ids
        self._clock = clock
        self._server_name = server_name
        self._refresher = refresher or ConcurrentExecutionRefresher(
            mcp=mcp,
            ids=ids,
            server_name=server_name,
        )
        self._jury = JuryEvaluator(integrity_policy)

    async def execute(self, request: ExecutionRequest) -> GuardedExecutionOutcome:
        async with self._sessions() as session, session.begin():
            planning_run = await session.get(PlanningRunModel, request.planning_run_id)
            if planning_run is None:
                raise ValueError("planning run not found")
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(
                            f"{planning_run.organization_id}:{request.idempotency_key}", 0
                        )
                    )
                )
            )
            duplicate = await session.scalar(
                select(ExecutionAuditModel).where(
                    ExecutionAuditModel.organization_id == planning_run.organization_id,
                    ExecutionAuditModel.idempotency_key == request.idempotency_key,
                )
            )
            if duplicate is not None:
                if (
                    duplicate.planning_run_id != request.planning_run_id
                    or duplicate.approved_plan_id != request.approved_plan_id
                    or duplicate.jury_decision_id != request.jury_evaluation_id
                    or duplicate.approval_policy_version != request.approval_policy_version
                    or duplicate.action != request.action
                ):
                    raise ValueError("idempotency key was reused for a different execution request")
                return GuardedExecutionOutcome(
                    result=ExecutionResult(
                        execution_id=duplicate.id,
                        state=ExecutionState.DUPLICATE,
                        attempted_at=duplicate.attempted_at or duplicate.requested_at,
                        completed_at=duplicate.completed_at,
                        external_references=tuple(duplicate.external_references),
                        failure_code=duplicate.failure_code,
                        detail="duplicate idempotency key",
                    ),
                    decision="duplicate",
                )

            if planning_run.status != JuryState.APPROVE.value:
                raise ValueError("planning run is not approved")
            if request.approval_policy_version != "approval-v1":
                raise ValueError("unsupported approval policy version")
            plan_row = await session.get(CandidatePlanModel, request.approved_plan_id)
            if plan_row is None:
                raise ValueError("approved plan not found")
            if plan_row.planning_run_id != planning_run.id:
                raise ValueError("approved plan does not belong to the planning run")
            if not plan_row.selected:
                raise ValueError("approved plan was not selected")
            if plan_row.feasibility == FeasibilityStatus.INFEASIBLE.value:
                raise ValueError("infeasible plans cannot be executed")
            plan = await _load_plan(session, request.approved_plan_id)
            if plan is None:
                raise ValueError("approved plan not found")
            await _validate_plan_organization(session, plan, planning_run.organization_id)
            jury_decision = await session.get(JuryDecisionModel, request.jury_evaluation_id)
            if jury_decision is None:
                raise ValueError("jury evaluation not found")
            if (
                jury_decision.planning_run_id != planning_run.id
                or jury_decision.plan_id != plan.plan_id
            ):
                raise ValueError("jury evaluation does not approve this planning run and plan")
            if jury_decision.policy_version != self._jury.policy.version:
                raise ValueError("jury evaluation uses an unsupported integrity policy")
            if not jury_decision.gate_results or any(
                not bool(gate.get("passed")) for gate in jury_decision.gate_results
            ):
                raise ValueError("jury evaluation has missing or failed hard gates")
            recorded_gate_codes = {
                str(gate.get("gate_code")) for gate in jury_decision.gate_results
            }
            if not recorded_gate_codes >= REQUIRED_JURY_GATES:
                raise ValueError("jury evaluation does not contain every required hard gate")
            if Decimal(jury_decision.integrity_score) < Decimal(
                str(self._jury.policy.approval_threshold)
            ):
                raise ValueError("jury evaluation is below the approval threshold")
            dissent_score = jury_decision.component_scores.get("dissent_robustness")
            if not isinstance(dissent_score, (int, float)) or dissent_score <= 0:
                raise ValueError("jury evaluation lacks a completed Dissent attestation")
            if jury_decision.final_state != JuryState.APPROVE.value:
                now = self._clock.now()
                return GuardedExecutionOutcome(
                    result=ExecutionResult(
                        execution_id=request.execution_id,
                        state=ExecutionState.FAILED,
                        attempted_at=now,
                        completed_at=now,
                        failure_code="jury_not_approved",
                        detail="execution requires an approved jury decision",
                    ),
                    decision="investigate",
                    reasons=("jury_not_approved",),
                )

            audit = ExecutionAuditModel(
                id=request.execution_id,
                organization_id=planning_run.organization_id,
                planning_run_id=request.planning_run_id,
                approved_plan_id=request.approved_plan_id,
                jury_decision_id=request.jury_evaluation_id,
                idempotency_key=request.idempotency_key,
                approval_policy_version=request.approval_policy_version,
                action=request.action,
                state=ExecutionState.PENDING.value,
                requested_at=request.requested_at,
                attempted_at=self._clock.now(),
                completed_at=None,
                failure_code=None,
                compensation_status=None,
                external_references=[],
            )
            session.add(audit)

            refresh = await self._refresher.refresh(
                request=request,
                plan=plan,
                organization_id=planning_run.organization_id,
            )
            readiness = self._assess_readiness(request, plan, refresh)
            if readiness.decision != "execute":
                audit.state = ExecutionState.FAILED.value
                audit.failure_code = readiness.result.failure_code
                audit.completed_at = readiness.result.completed_at
                return readiness

            inventory = InventoryService(session)
            try:
                for line in plan.distribution:
                    await inventory.reserve_fefo(
                        organization_id=planning_run.organization_id,
                        sku_id=line.sku_id,
                        warehouse_id=line.source_warehouse_id,
                        quantity=line.quantity.value,
                        occurred_at=self._clock.now(),
                        business_reference=request.execution_id,
                        idempotency_key=(
                            f"{request.idempotency_key}:{line.sku_id}:{line.source_warehouse_id}"
                        ),
                    )
            except (DuplicateReservation, InsufficientInventory) as error:
                now = self._clock.now()
                audit.state = ExecutionState.FAILED.value
                audit.failure_code = "inventory_reservation_failed"
                audit.completed_at = now
                return GuardedExecutionOutcome(
                    result=ExecutionResult(
                        execution_id=request.execution_id,
                        state=ExecutionState.FAILED,
                        attempted_at=audit.attempted_at or request.requested_at,
                        completed_at=now,
                        failure_code="inventory_reservation_failed",
                        detail=str(error),
                    ),
                    decision="investigate",
                    reasons=("inventory_reservation_failed",),
                    required_investigation=("Revalidate inventory availability.",),
                )

            external_references: list[str] = []
            try:
                for supplier_id, lines in _group_procurement_lines(plan).items():
                    result = await self._mcp.invoke(
                        MCPToolCall(
                            call_id=self._ids.new_id("mcp"),
                            server_name=self._server_name,
                            tool_name="create_procurement_order",
                            arguments={
                                "planning_run_id": request.planning_run_id,
                                "supplier_id": supplier_id,
                                "lines": lines,
                            },
                            access_mode=MCPAccessMode.WRITE,
                            idempotency_key=f"{request.idempotency_key}:{supplier_id}",
                        )
                    )
                    order_id = result.payload.get("order_id")
                    if isinstance(order_id, str):
                        external_references.append(order_id)
            except Exception:
                now = self._clock.now()
                audit.state = ExecutionState.COMPENSATION_REQUIRED.value
                audit.failure_code = "provider_write_failed"
                audit.compensation_status = "required"
                audit.external_references = external_references
                audit.completed_at = now
                return GuardedExecutionOutcome(
                    result=ExecutionResult(
                        execution_id=request.execution_id,
                        state=ExecutionState.COMPENSATION_REQUIRED,
                        attempted_at=audit.attempted_at or request.requested_at,
                        completed_at=now,
                        external_references=tuple(external_references),
                        failure_code="provider_write_failed",
                        detail="execution wrote partial external state",
                    ),
                    decision="compensate",
                    reasons=("provider_write_failed",),
                )

            now = self._clock.now()
            audit.state = ExecutionState.SUCCEEDED.value
            audit.external_references = external_references
            audit.completed_at = now
            return GuardedExecutionOutcome(
                result=ExecutionResult(
                    execution_id=request.execution_id,
                    state=ExecutionState.SUCCEEDED,
                    attempted_at=audit.attempted_at or request.requested_at,
                    completed_at=now,
                    external_references=tuple(external_references),
                ),
                decision="execute",
            )

    def _assess_readiness(
        self,
        request: ExecutionRequest,
        plan: CandidatePlan,
        refresh: RefreshBundle,
    ) -> GuardedExecutionOutcome:
        now = self._clock.now()
        stale_claim_ids = tuple(
            claim.claim_id
            for claim in refresh.claims
            if claim.valid_at is not None and now - claim.valid_at > _ttl_for(claim.predicate)
        )
        if stale_claim_ids:
            return GuardedExecutionOutcome(
                result=ExecutionResult(
                    execution_id=request.execution_id,
                    state=ExecutionState.FAILED,
                    attempted_at=now,
                    completed_at=now,
                    failure_code="stale_execution_data",
                    detail="Execution Freshness v1 blocked stale inputs.",
                ),
                decision="investigate",
                reasons=("stale_execution_data",),
                required_investigation=tuple(
                    f"Refresh execution-critical claim {claim_id}." for claim_id in stale_claim_ids
                ),
            )

        approved_total = sum((line.landed_cost for line in plan.procurement), Decimal("0"))
        refreshed_total = Decimal("0")
        required: list[str] = []
        for line in plan.procurement:
            key = _offer_key(
                {
                    "supplier_id": line.supplier_id,
                    "sku_id": line.sku_id,
                    "destination_warehouse_id": line.destination_warehouse_id,
                }
            )
            unit_price = refresh.observed_unit_prices.get(key)
            available = refresh.constraints.get(f"offer:{key}:available", Decimal("0"))
            if unit_price is None or available < line.quantity.value:
                required.append(f"Regenerate solver plan for supplier offer {key}.")
                continue
            refreshed_total += unit_price * line.quantity.value
        for distribution_line in plan.distribution:
            warehouse_key = (
                f"warehouse:{distribution_line.destination_warehouse_id}:{distribution_line.sku_id}"
            )
            if (
                refresh.constraints.get(warehouse_key, Decimal("0"))
                < distribution_line.quantity.value
            ):
                required.append(f"Regenerate solver plan for warehouse capacity {warehouse_key}.")
            transport_key = f"transport:{_transport_key_from_line(distribution_line)}"
            if (
                refresh.constraints.get(transport_key, Decimal("0"))
                < distribution_line.quantity.value
            ):
                required.append(f"Regenerate solver plan for transport lane {transport_key}.")
        if required:
            return GuardedExecutionOutcome(
                result=ExecutionResult(
                    execution_id=request.execution_id,
                    state=ExecutionState.FAILED,
                    attempted_at=now,
                    completed_at=now,
                    failure_code="plan_changed",
                    detail="Final refresh changed execution feasibility.",
                ),
                decision="investigate",
                reasons=("plan_changed",),
                required_investigation=tuple(dict.fromkeys(required)),
            )
        if refreshed_total > approved_total:
            return GuardedExecutionOutcome(
                result=ExecutionResult(
                    execution_id=request.execution_id,
                    state=ExecutionState.FAILED,
                    attempted_at=now,
                    completed_at=now,
                    failure_code="approved_total_exceeded",
                    detail="Refreshed total exceeds the approved total.",
                ),
                decision="escalate",
                reasons=("approved_total_exceeded",),
            )

        dissent_protocol = DissentProtocol()
        dissent = dissent_protocol.record_plan(
            DissentInvestigationPlan(
                context_id="execution-integrity-revalidation",
                memory_namespace="execution-integrity-memory",
                tool_cache_namespace="execution-integrity-cache",
                checks=("carry forward approved Dissent and refresh mutable execution facts",),
                tool_budget=max(1, len(refresh.evidence)),
            )
        )
        dissent = dissent_protocol.record_fresh_retrieval(
            dissent,
            evidence_ids=tuple(record.evidence_id for record in refresh.evidence),
        )
        dissent = dissent_protocol.compare_with_existing_graph(
            dissent,
            checked_claim_ids=tuple(claim.claim_id for claim in refresh.claims),
        )
        evaluation = self._jury.evaluate(
            JuryRequest(
                planning_run_id=request.planning_run_id,
                candidate_plan=plan,
                supporting_claim_ids=tuple(claim.claim_id for claim in refresh.claims),
                evidence_ids=tuple(record.evidence_id for record in refresh.evidence),
                policy_version=self._jury.policy.version,
            ),
            JuryInputs(
                claims=refresh.claims,
                evidence=refresh.evidence,
                dissent=dissent,
                gate_facts=GateFacts(stale_execution_claim_ids=stale_claim_ids),
            ),
            evaluation_id=self._ids.new_id("jury"),
            calculated_at=now,
        )
        if evaluation.state is JuryState.APPROVE:
            return GuardedExecutionOutcome(
                result=ExecutionResult(
                    execution_id=request.execution_id,
                    state=ExecutionState.PENDING,
                    attempted_at=now,
                ),
                decision="execute",
            )
        return GuardedExecutionOutcome(
            result=ExecutionResult(
                execution_id=request.execution_id,
                state=ExecutionState.FAILED,
                attempted_at=now,
                completed_at=now,
                failure_code="jury_blocked_execution",
                detail="Final integrity rerun did not approve execution.",
            ),
            decision=evaluation.state.value,
            reasons=evaluation.reason_codes,
            required_investigation=evaluation.required_investigation,
        )


async def _load_plan(session: AsyncSession, plan_id: str) -> CandidatePlan | None:
    plan_row = await session.get(CandidatePlanModel, plan_id)
    if plan_row is None:
        return None
    procurement_rows = (
        await session.scalars(
            select(ProcurementLineModel).where(ProcurementLineModel.plan_id == plan_id)
        )
    ).all()
    distribution_rows = (
        await session.scalars(
            select(DistributionLineModel).where(DistributionLineModel.plan_id == plan_id)
        )
    ).all()
    return CandidatePlan(
        plan_id=plan_row.id,
        planning_run_id=plan_row.planning_run_id,
        feasibility=FeasibilityStatus(plan_row.feasibility),
        procurement=tuple(
            ProcurementLine(
                supplier_id=row.supplier_id,
                sku_id=row.sku_id,
                destination_warehouse_id=row.destination_warehouse_id,
                arrival_bucket_start=row.arrival_bucket_start,
                quantity=Quantity(value=row.quantity, unit=row.unit_of_measure),
                landed_cost=row.landed_cost,
            )
            for row in procurement_rows
        ),
        distribution=tuple(
            DistributionLine(
                sku_id=row.sku_id,
                source_warehouse_id=row.source_warehouse_id,
                destination_warehouse_id=row.destination_warehouse_id,
                departure_bucket_start=row.departure_bucket_start,
                arrival_bucket_start=row.arrival_bucket_start,
                quantity=Quantity(value=row.quantity, unit=row.unit_of_measure),
                source_lot_ids=tuple(row.source_lot_ids),
            )
            for row in distribution_rows
        ),
        shortage_base_units=plan_row.shortage_base_units,
        metrics={key: Decimal(str(value)) for key, value in plan_row.metrics.items()},
        solver_version=plan_row.solver_version,
    )


async def _validate_plan_organization(
    session: AsyncSession,
    plan: CandidatePlan,
    organization_id: str,
) -> None:
    """Reject cross-tenant object references before reserving or writing externally."""

    supplier_ids = {line.supplier_id for line in plan.procurement}
    sku_ids = {line.sku_id for line in plan.procurement}
    sku_ids.update(line.sku_id for line in plan.distribution)
    warehouse_ids = {
        warehouse_id
        for line in plan.procurement
        for warehouse_id in (line.destination_warehouse_id,)
    }
    warehouse_ids.update(
        warehouse_id
        for line in plan.distribution
        for warehouse_id in (line.source_warehouse_id, line.destination_warehouse_id)
    )
    lot_ids = {lot_id for line in plan.distribution for lot_id in line.source_lot_ids}

    owned_supplier_ids = set(
        await session.scalars(
            select(SupplierModel.id).where(
                SupplierModel.id.in_(supplier_ids),
                SupplierModel.organization_id == organization_id,
            )
        )
    )
    owned_sku_ids = set(
        await session.scalars(
            select(SKUModel.id).where(
                SKUModel.id.in_(sku_ids),
                SKUModel.organization_id == organization_id,
            )
        )
    )
    owned_warehouse_ids = set(
        await session.scalars(
            select(WarehouseModel.id).where(
                WarehouseModel.id.in_(warehouse_ids),
                WarehouseModel.organization_id == organization_id,
            )
        )
    )
    owned_lot_ids = set(
        await session.scalars(
            select(InventoryLotModel.id).where(
                InventoryLotModel.id.in_(lot_ids),
                InventoryLotModel.organization_id == organization_id,
            )
        )
    )
    for owned, expected, label in (
        (owned_supplier_ids, supplier_ids, "supplier"),
        (owned_sku_ids, sku_ids, "SKU"),
        (owned_warehouse_ids, warehouse_ids, "warehouse"),
        (owned_lot_ids, lot_ids, "inventory lot"),
    ):
        if owned != expected:
            raise ValueError(f"plan contains an unknown or cross-organization {label}")


def _group_procurement_lines(plan: CandidatePlan) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in plan.procurement:
        grouped[line.supplier_id].append(
            {
                "supplier_id": line.supplier_id,
                "sku_id": line.sku_id,
                "destination_warehouse_id": line.destination_warehouse_id,
                "quantity": str(line.quantity.value),
                "unit_of_measure": line.quantity.unit,
                "landed_cost": str(line.landed_cost),
            }
        )
    return grouped


def _offer_key(record: Mapping[str, Any]) -> str:
    return (
        f"{record.get('supplier_id')}:{record.get('sku_id')}:"
        f"{record.get('destination_warehouse_id')}"
    )


def _transport_key(record: Mapping[str, Any]) -> str:
    return (
        f"{record.get('source_warehouse_id')}:"
        f"{record.get('destination_warehouse_id')}:"
        f"{record.get('sku_id')}"
    )


def _transport_key_from_line(line: DistributionLine) -> str:
    return f"{line.source_warehouse_id}:{line.destination_warehouse_id}:{line.sku_id}"


def _records(payload: Mapping[str, Any], key: str) -> Sequence[Mapping[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _optional_str(record: Mapping[str, Any], key: str) -> str | None:
    value = record.get(key)
    return None if value is None else str(value)


def _ttl_for(predicate: str) -> timedelta:
    policy = IntegrityPolicyV1()
    return policy.freshness_ttls.get(predicate, policy.default_freshness_ttl)
