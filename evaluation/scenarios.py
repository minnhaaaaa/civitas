"""Scenario manifest schema and deterministic golden bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from civitas.contracts import (
    CandidatePlan,
    ClaimScope,
    EvidenceIdentity,
    EvidenceOrigin,
    EvidenceRecord,
    ExecutionRequest,
    FeasibilityStatus,
    JuryRequest,
    TypedClaim,
)
from civitas.contracts.enums import ExecutionState, JuryState
from civitas.evidence import (
    DissentInvestigationPlan,
    DissentProtocol,
    GateFacts,
    JuryInputs,
    ReasonCode,
)
from civitas.optimization import (
    Demand,
    InventoryLot,
    OptimizationProblem,
    PlanningBucket,
    SupplierOffer,
)

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ScenarioManifest:
    scenario_id: str
    title: str
    version: str
    deterministic_seed: int
    tags: tuple[str, ...]
    calculated_at: datetime
    small_case_oracle: bool = True


@dataclass(frozen=True, slots=True)
class HiddenWorldState:
    hidden_claims: tuple[TypedClaim, ...] = ()
    hidden_evidence: tuple[EvidenceRecord, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisibleObservations:
    problem: OptimizationProblem
    jury_request: JuryRequest
    jury_inputs: JuryInputs
    candidate_plan_override: CandidatePlan | None = None
    execution_request: ExecutionRequest | None = None
    retry_execution: bool = False

    @property
    def effective_critical_claim_ids(self) -> frozenset[str]:
        return (
            self.jury_inputs.critical_claim_ids
            if self.jury_inputs.critical_claim_ids is not None
            else frozenset(self.jury_request.supporting_claim_ids)
        )


@dataclass(frozen=True, slots=True)
class ExpectedEvidenceLineage:
    claim_source_groups: dict[str, frozenset[str]]
    contradiction_ids: frozenset[str] = frozenset()
    incomplete_lineage_evidence_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ExpectedOutcome:
    solver_status: FeasibilityStatus
    jury_state: JuryState
    reason_codes: frozenset[str]
    optimal_weighted_shortage: int | None
    minimum_alternatives: int = 1
    selected_maximum_regret_ceiling: Decimal | None = None
    selected_total_regret_ceiling: Decimal | None = None
    execution_state: ExecutionState | None = None


@dataclass(frozen=True, slots=True)
class InterventionStep:
    step_id: str
    description: str
    reveals_claim_ids: tuple[str, ...] = ()
    reveals_evidence_ids: tuple[str, ...] = ()
    expected_reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GoldenScenario:
    manifest: ScenarioManifest
    true_world_state: HiddenWorldState
    visible: VisibleObservations
    expected_lineage: ExpectedEvidenceLineage
    expected_outcome: ExpectedOutcome
    intervention_sequence: tuple[InterventionStep, ...] = ()


def get_scenario(scenario_id: str) -> GoldenScenario:
    for scenario in ALL_SCENARIOS:
        if scenario.manifest.scenario_id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def _bucket() -> PlanningBucket:
    return PlanningBucket("day-1", NOW, NOW + timedelta(days=1), urgency=1)


def _problem(
    *,
    demand_quantity: int,
    inventory_quantity: int = 0,
    offers: tuple[SupplierOffer, ...] = (),
) -> OptimizationProblem:
    bucket = _bucket()
    return OptimizationProblem(
        planning_run_id="run-1",
        buckets=(bucket,),
        demands=(Demand("demand-1", "sku-1", "warehouse-1", bucket.bucket_id, demand_quantity),),
        inventory_lots=(
            InventoryLot(
                "lot-1",
                "sku-1",
                "warehouse-1",
                inventory_quantity,
                bucket.end + timedelta(days=2),
            ),
        )
        if inventory_quantity
        else (),
        supplier_offers=offers,
        maximum_alternatives=5,
    )


def _offer(
    offer_id: str,
    supplier_id: str,
    *,
    capacity: int,
    unit_cost: int,
    risk: int = 0,
    expected_waste_rate: int = 0,
) -> SupplierOffer:
    return SupplierOffer(
        offer_id=offer_id,
        supplier_id=supplier_id,
        sku_id="sku-1",
        destination_warehouse_id="warehouse-1",
        arrival_bucket_id="day-1",
        capacity=capacity,
        unit_cost=unit_cost,
        risk=risk,
        expected_waste_rate=expected_waste_rate,
    )


def _claim(
    claim_id: str,
    predicate: str,
    value: int | str,
    *,
    supplier_id: str | None = None,
    warehouse_id: str | None = None,
) -> TypedClaim:
    return TypedClaim(
        claim_id=claim_id,
        subject=supplier_id or warehouse_id or "sku-1",
        predicate=predicate,
        value=value,
        unit="unit",
        valid_at=NOW,
        scope=ClaimScope(
            organization_id="org-1",
            supplier_id=supplier_id,
            warehouse_id=warehouse_id,
            sku_id="sku-1",
        ),
        human_summary=f"{predicate}={value}",
    )


def _evidence(
    evidence_id: str,
    *,
    claim_ids: tuple[str, ...],
    source_id: str,
    source_type: str,
    retrieved_at: datetime = NOW,
    derived_from: tuple[str, ...] = (),
    origin: EvidenceOrigin = EvidenceOrigin.EXTERNAL,
    agent_id: str | None = None,
    mcp_server: str | None = "mcp",
    tool_name: str | None = "get",
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        claim_ids=claim_ids,
        identity=EvidenceIdentity(
            canonical_source_id=source_id,
            canonical_source_type=source_type,
            mcp_server=mcp_server,
            tool_name=tool_name,
            normalized_arguments={"id": source_id},
            retrieved_at=retrieved_at,
            observation_version="v1",
            raw_response_sha256="a" * 64,
        ),
        origin=origin,
        agent_id=agent_id,
        content_summary=f"{source_type}:{source_id}",
        derived_from=derived_from,
    )


def _dissent(*, invalidates: bool = False):
    protocol = DissentProtocol()
    report = protocol.record_plan(
        DissentInvestigationPlan(
            context_id="dissent-thread",
            memory_namespace="dissent-memory",
            tool_cache_namespace="dissent-cache",
            checks=("verify critical claims",),
            tool_budget=2,
        )
    )
    report = protocol.record_fresh_retrieval(report, evidence_ids=("fresh-1",))
    return protocol.compare_with_existing_graph(
        report,
        checked_claim_ids=("c-lead",),
        contradiction_ids=("contradiction:c-lead:c-lead-live",) if invalidates else (),
        establishes_invalidity=invalidates,
    )


def _jury_request(
    *, evidence_ids: tuple[str, ...], supporting_claim_ids: tuple[str, ...]
) -> JuryRequest:
    return JuryRequest(
        planning_run_id="run-1",
        candidate_plan=CandidatePlan(
            plan_id="placeholder",
            planning_run_id="run-1",
            feasibility=FeasibilityStatus.FULLY_FEASIBLE,
            shortage_base_units=0,
            solver_version="placeholder",
        ),
        supporting_claim_ids=supporting_claim_ids,
        evidence_ids=evidence_ids,
        policy_version="decision-integrity-v1",
    )


def _scenario_independent_consensus() -> GoldenScenario:
    claims = (
        _claim("c-lead", "lead_time", 2, supplier_id="supplier-a"),
        _claim("c-price", "unit_price", 5, supplier_id="supplier-a"),
    )
    evidence = (
        _evidence(
            "e-lead", claim_ids=("c-lead",), source_id="supplier-a-live", source_type="supplier_api"
        ),
        _evidence(
            "e-price", claim_ids=("c-price",), source_id="pricebook-1", source_type="price_feed"
        ),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "independent-consensus",
            "Independent consensus",
            "2026-08-27.1",
            101,
            ("golden", "consensus"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            notes=("Visible observations already match ground truth.",)
        ),
        visible=VisibleObservations(
            problem=_problem(
                demand_quantity=5,
                inventory_quantity=3,
                offers=(_offer("offer-a", "supplier-a", capacity=2, unit_cost=5),),
            ),
            jury_request=_jury_request(
                evidence_ids=("e-lead", "e-price"), supporting_claim_ids=("c-lead", "c-price")
            ),
            jury_inputs=JuryInputs(claims=claims, evidence=evidence, dissent=_dissent()),
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={
                "c-lead": frozenset({"supplier_api:supplier-a-live"}),
                "c-price": frozenset({"price_feed:pricebook-1"}),
            }
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.FULLY_FEASIBLE,
            jury_state=JuryState.APPROVE,
            reason_codes=frozenset(),
            optimal_weighted_shortage=0,
        ),
    )


def _scenario_shared_source_false_consensus() -> GoldenScenario:
    claims = (_claim("c-lead", "lead_time", 2, supplier_id="supplier-a"),)
    evidence = (
        _evidence(
            "e-primary",
            claim_ids=("c-lead",),
            source_id="supplier-a-master",
            source_type="supplier_api",
        ),
        _evidence(
            "e-echo",
            claim_ids=("c-lead",),
            source_id="agent-synthesis",
            source_type="agent_summary",
            origin=EvidenceOrigin.AGENT_DERIVED,
            agent_id="cost-agent",
            derived_from=("e-primary",),
            mcp_server=None,
            tool_name=None,
        ),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "shared-source-false-consensus",
            "Shared-source false consensus",
            "2026-08-27.1",
            102,
            ("golden", "shared-source"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            notes=("Two apparent supports collapse to one upstream source.",)
        ),
        visible=VisibleObservations(
            problem=_problem(
                demand_quantity=4,
                inventory_quantity=2,
                offers=(_offer("offer-a", "supplier-a", capacity=2, unit_cost=4),),
            ),
            jury_request=_jury_request(
                evidence_ids=("e-primary", "e-echo"), supporting_claim_ids=("c-lead",)
            ),
            jury_inputs=JuryInputs(claims=claims, evidence=evidence, dissent=_dissent()),
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={"c-lead": frozenset({"supplier_api:supplier-a-master"})}
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.FULLY_FEASIBLE,
            jury_state=JuryState.INVESTIGATE,
            reason_codes=frozenset({ReasonCode.INTEGRITY_BELOW_APPROVAL_THRESHOLD.value}),
            optimal_weighted_shortage=0,
        ),
        intervention_sequence=(
            InterventionStep(
                step_id="refresh-second-source",
                description="Collect a genuinely independent lead-time source before approval.",
                expected_reason_codes=(ReasonCode.INTEGRITY_BELOW_APPROVAL_THRESHOLD.value,),
            ),
        ),
    )


def _scenario_agent_echo() -> GoldenScenario:
    hidden_evidence = (
        _evidence(
            "e-root",
            claim_ids=("c-lead",),
            source_id="supplier-a-master",
            source_type="supplier_api",
        ),
    )
    visible_evidence = (
        _evidence(
            "e-echo",
            claim_ids=("c-lead",),
            source_id="agent-synthesis",
            source_type="agent_summary",
            origin=EvidenceOrigin.AGENT_DERIVED,
            agent_id="freshness-agent",
            derived_from=("e-root",),
        ),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "agent-echo",
            "Agent echo chain",
            "2026-08-27.1",
            103,
            ("golden", "echo"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            hidden_evidence=hidden_evidence,
            notes=(
                "The visible fixture contains only the echo, not the originating external record.",
            ),
        ),
        visible=VisibleObservations(
            problem=_problem(
                demand_quantity=3,
                inventory_quantity=1,
                offers=(_offer("offer-a", "supplier-a", capacity=2, unit_cost=4),),
            ),
            jury_request=_jury_request(evidence_ids=("e-echo",), supporting_claim_ids=("c-lead",)),
            jury_inputs=JuryInputs(
                claims=(_claim("c-lead", "lead_time", 2, supplier_id="supplier-a"),),
                evidence=visible_evidence,
                dissent=_dissent(),
            ),
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={"c-lead": frozenset()},
            incomplete_lineage_evidence_ids=frozenset({"e-echo"}),
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.FULLY_FEASIBLE,
            jury_state=JuryState.INVESTIGATE,
            reason_codes=frozenset({ReasonCode.CRITICAL_CLAIM_UNSUPPORTED.value}),
            optimal_weighted_shortage=0,
        ),
        intervention_sequence=(
            InterventionStep(
                step_id="recover-origin",
                description="Recover the missing external source before the claim can be trusted.",
                reveals_evidence_ids=("e-root",),
                expected_reason_codes=(ReasonCode.CRITICAL_CLAIM_UNSUPPORTED.value,),
            ),
        ),
    )


def _scenario_stale_contradiction() -> GoldenScenario:
    claims = (
        _claim("c-lead", "lead_time", 2, supplier_id="supplier-a"),
        _claim("c-lead-live", "lead_time", 10, supplier_id="supplier-a"),
    )
    evidence = (
        _evidence(
            "e-stale",
            claim_ids=("c-lead",),
            source_id="supplier-a-master",
            source_type="supplier_api",
            retrieved_at=NOW - timedelta(minutes=20),
        ),
        _evidence(
            "e-live",
            claim_ids=("c-lead-live",),
            source_id="supplier-a-live",
            source_type="supplier_api",
        ),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "stale-lead-time-contradiction",
            "Stale lead-time contradiction",
            "2026-08-27.1",
            104,
            ("golden", "stale", "contradiction"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            notes=("A fresh source contradicts the stale operational assumption.",)
        ),
        visible=VisibleObservations(
            problem=_problem(
                demand_quantity=4,
                inventory_quantity=1,
                offers=(_offer("offer-a", "supplier-a", capacity=3, unit_cost=4),),
            ),
            jury_request=_jury_request(
                evidence_ids=("e-stale", "e-live"), supporting_claim_ids=("c-lead",)
            ),
            jury_inputs=JuryInputs(
                claims=claims,
                evidence=evidence,
                dissent=_dissent(),
                gate_facts=GateFacts(stale_execution_claim_ids=("c-lead",)),
            ),
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={
                "c-lead": frozenset({"supplier_api:supplier-a-master"}),
            },
            contradiction_ids=frozenset({"contradiction:c-lead:c-lead-live"}),
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.FULLY_FEASIBLE,
            jury_state=JuryState.INVESTIGATE,
            reason_codes=frozenset(
                {
                    ReasonCode.CRITICAL_CONTRADICTION_UNRESOLVED.value,
                    ReasonCode.STALE_EXECUTION_DATA.value,
                }
            ),
            optimal_weighted_shortage=0,
        ),
        intervention_sequence=(
            InterventionStep(
                step_id="refresh-lead-time",
                description="Refresh supplier A lead time from a current operational source.",
                reveals_claim_ids=("c-lead-live",),
                expected_reason_codes=(
                    ReasonCode.CRITICAL_CONTRADICTION_UNRESOLVED.value,
                    ReasonCode.STALE_EXECUTION_DATA.value,
                ),
            ),
        ),
    )


def _scenario_clean_mcp_evidence() -> GoldenScenario:
    claims = (
        _claim("c-lead", "lead_time", 1, supplier_id="supplier-b"),
        _claim("c-inventory", "inventory_balance", 4, warehouse_id="warehouse-1"),
    )
    evidence = (
        _evidence(
            "e-lead", claim_ids=("c-lead",), source_id="supplier-b-live", source_type="supplier_api"
        ),
        _evidence(
            "e-inventory",
            claim_ids=("c-inventory",),
            source_id="warehouse-1-ledger",
            source_type="inventory_api",
        ),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "clean-mcp-evidence",
            "Clean current MCP evidence",
            "2026-08-27.1",
            105,
            ("golden", "mcp"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            notes=("Fresh supplier and inventory data agree with the plan.",)
        ),
        visible=VisibleObservations(
            problem=_problem(
                demand_quantity=4,
                inventory_quantity=2,
                offers=(_offer("offer-b", "supplier-b", capacity=2, unit_cost=5),),
            ),
            jury_request=_jury_request(
                evidence_ids=("e-lead", "e-inventory"),
                supporting_claim_ids=("c-lead", "c-inventory"),
            ),
            jury_inputs=JuryInputs(claims=claims, evidence=evidence, dissent=_dissent()),
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={
                "c-lead": frozenset({"supplier_api:supplier-b-live"}),
                "c-inventory": frozenset({"inventory_api:warehouse-1-ledger"}),
            }
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.FULLY_FEASIBLE,
            jury_state=JuryState.APPROVE,
            reason_codes=frozenset(),
            optimal_weighted_shortage=0,
        ),
    )


def _scenario_objective_conflict() -> GoldenScenario:
    claims = (
        _claim("c-lead", "lead_time", 1, supplier_id="supplier-a"),
        _claim("c-price", "unit_price", 3, supplier_id="supplier-a"),
    )
    evidence = (
        _evidence(
            "e-lead", claim_ids=("c-lead",), source_id="supplier-a-live", source_type="supplier_api"
        ),
        _evidence(
            "e-price", claim_ids=("c-price",), source_id="pricebook-2", source_type="price_feed"
        ),
    )
    offers = (
        _offer(
            "offer-cheap", "supplier-a", capacity=6, unit_cost=3, risk=10, expected_waste_rate=5
        ),
        _offer("offer-safe", "supplier-b", capacity=6, unit_cost=6, risk=0, expected_waste_rate=0),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "genuine-objective-conflict",
            "Genuine objective conflict",
            "2026-08-27.1",
            106,
            ("golden", "tradeoff"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            notes=("No single option dominates cost, risk, and waste simultaneously.",)
        ),
        visible=VisibleObservations(
            problem=_problem(demand_quantity=6, offers=offers),
            jury_request=_jury_request(
                evidence_ids=("e-lead", "e-price"), supporting_claim_ids=("c-lead", "c-price")
            ),
            jury_inputs=JuryInputs(claims=claims, evidence=evidence, dissent=_dissent()),
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={
                "c-lead": frozenset({"supplier_api:supplier-a-live"}),
                "c-price": frozenset({"price_feed:pricebook-2"}),
            }
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.FULLY_FEASIBLE,
            jury_state=JuryState.APPROVE,
            reason_codes=frozenset(),
            optimal_weighted_shortage=0,
            minimum_alternatives=2,
            selected_maximum_regret_ceiling=Decimal("100"),
            selected_total_regret_ceiling=Decimal("600"),
        ),
    )


def _scenario_partial_fulfillment() -> GoldenScenario:
    claims = (_claim("c-lead", "lead_time", 1, supplier_id="supplier-c"),)
    evidence = (
        _evidence(
            "e-lead", claim_ids=("c-lead",), source_id="supplier-c-live", source_type="supplier_api"
        ),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "partial-fulfillment",
            "Partial fulfillment",
            "2026-08-27.1",
            107,
            ("golden", "shortage"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            notes=("Inputs are sound but total available supply cannot satisfy all demand.",)
        ),
        visible=VisibleObservations(
            problem=_problem(
                demand_quantity=10,
                inventory_quantity=2,
                offers=(_offer("offer-c", "supplier-c", capacity=3, unit_cost=4),),
            ),
            jury_request=_jury_request(evidence_ids=("e-lead",), supporting_claim_ids=("c-lead",)),
            jury_inputs=JuryInputs(claims=claims, evidence=evidence, dissent=_dissent()),
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={"c-lead": frozenset({"supplier_api:supplier-c-live"})}
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.PARTIALLY_FULFILLED,
            jury_state=JuryState.APPROVE,
            reason_codes=frozenset(),
            optimal_weighted_shortage=5,
        ),
    )


def _scenario_fefo_failure() -> GoldenScenario:
    claims = (_claim("c-lead", "lead_time", 1, supplier_id="supplier-d"),)
    evidence = (
        _evidence(
            "e-lead", claim_ids=("c-lead",), source_id="supplier-d-live", source_type="supplier_api"
        ),
    )
    bucket = _bucket()
    problem = OptimizationProblem(
        planning_run_id="run-1",
        buckets=(bucket,),
        demands=(Demand("demand-1", "sku-1", "warehouse-1", bucket.bucket_id, 4),),
        inventory_lots=(
            InventoryLot("lot-old", "sku-1", "warehouse-1", 2, bucket.end + timedelta(days=1)),
            InventoryLot("lot-new", "sku-1", "warehouse-1", 2, bucket.end + timedelta(days=3)),
        ),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "fefo-failure",
            "FEFO failure",
            "2026-08-27.1",
            108,
            ("golden", "fefo"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            notes=("The candidate plan consumed newer stock before earlier-expiring stock.",)
        ),
        visible=VisibleObservations(
            problem=problem,
            jury_request=_jury_request(evidence_ids=("e-lead",), supporting_claim_ids=("c-lead",)),
            jury_inputs=JuryInputs(
                claims=claims,
                evidence=evidence,
                dissent=_dissent(),
                gate_facts=GateFacts(
                    hard_constraint_violations=("FEFO violation: lot-new used before lot-old",)
                ),
            ),
            candidate_plan_override=CandidatePlan(
                plan_id="manual-fefo-plan",
                planning_run_id="run-1",
                feasibility=FeasibilityStatus.FULLY_FEASIBLE,
                shortage_base_units=0,
                solver_version="manual",
            ),
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={"c-lead": frozenset({"supplier_api:supplier-d-live"})}
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.FULLY_FEASIBLE,
            jury_state=JuryState.REJECT,
            reason_codes=frozenset({ReasonCode.HARD_CONSTRAINT_VIOLATION.value}),
            optimal_weighted_shortage=0,
        ),
    )


def _scenario_capacity_conflict() -> GoldenScenario:
    claims = (_claim("c-capacity", "warehouse_capacity", 2, warehouse_id="warehouse-1"),)
    evidence = (
        _evidence(
            "e-capacity",
            claim_ids=("c-capacity",),
            source_id="warehouse-1-capacity",
            source_type="inventory_api",
        ),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "warehouse-capacity-conflict",
            "Warehouse-capacity conflict",
            "2026-08-27.1",
            109,
            ("golden", "capacity"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            notes=("A proposed inbound quantity exceeds the warehouse's declared capacity.",)
        ),
        visible=VisibleObservations(
            problem=_problem(
                demand_quantity=3,
                offers=(_offer("offer-cap", "supplier-e", capacity=3, unit_cost=4),),
            ),
            jury_request=_jury_request(
                evidence_ids=("e-capacity",), supporting_claim_ids=("c-capacity",)
            ),
            jury_inputs=JuryInputs(
                claims=claims,
                evidence=evidence,
                dissent=_dissent(),
                gate_facts=GateFacts(
                    hard_constraint_violations=("warehouse warehouse-1 exceeds capacity in day-1",)
                ),
            ),
            candidate_plan_override=CandidatePlan(
                plan_id="manual-capacity-plan",
                planning_run_id="run-1",
                feasibility=FeasibilityStatus.FULLY_FEASIBLE,
                shortage_base_units=0,
                solver_version="manual",
            ),
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={"c-capacity": frozenset({"inventory_api:warehouse-1-capacity"})}
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.FULLY_FEASIBLE,
            jury_state=JuryState.REJECT,
            reason_codes=frozenset({ReasonCode.HARD_CONSTRAINT_VIOLATION.value}),
            optimal_weighted_shortage=0,
        ),
    )


def _scenario_duplicate_retry() -> GoldenScenario:
    claims = (_claim("c-lead", "lead_time", 1, supplier_id="supplier-f"),)
    evidence = (
        _evidence(
            "e-lead", claim_ids=("c-lead",), source_id="supplier-f-live", source_type="supplier_api"
        ),
    )
    return GoldenScenario(
        manifest=ScenarioManifest(
            "duplicate-execution-retry",
            "Duplicate execution retry",
            "2026-08-27.1",
            110,
            ("golden", "execution"),
            NOW + timedelta(minutes=1),
        ),
        true_world_state=HiddenWorldState(
            notes=("The second write attempt should be blocked by the idempotency ledger.",)
        ),
        visible=VisibleObservations(
            problem=_problem(
                demand_quantity=2,
                offers=(_offer("offer-f", "supplier-f", capacity=2, unit_cost=4),),
            ),
            jury_request=_jury_request(evidence_ids=("e-lead",), supporting_claim_ids=("c-lead",)),
            jury_inputs=JuryInputs(claims=claims, evidence=evidence, dissent=_dissent()),
            execution_request=ExecutionRequest(
                execution_id="exec-1",
                planning_run_id="run-1",
                approved_plan_id="approved-plan",
                jury_evaluation_id="eval-1",
                idempotency_key="same-key",
                approval_policy_version="approval-v1",
                requested_at=NOW + timedelta(minutes=1),
                action={"type": "create_procurement_order"},
            ),
            retry_execution=True,
        ),
        expected_lineage=ExpectedEvidenceLineage(
            claim_source_groups={"c-lead": frozenset({"supplier_api:supplier-f-live"})}
        ),
        expected_outcome=ExpectedOutcome(
            solver_status=FeasibilityStatus.FULLY_FEASIBLE,
            jury_state=JuryState.APPROVE,
            reason_codes=frozenset(),
            optimal_weighted_shortage=0,
            execution_state=ExecutionState.DUPLICATE,
        ),
        intervention_sequence=(
            InterventionStep(
                step_id="retry-write",
                description="Retry the same MCP write with the same idempotency key.",
            ),
        ),
    )


ALL_SCENARIOS: tuple[GoldenScenario, ...] = (
    _scenario_independent_consensus(),
    _scenario_shared_source_false_consensus(),
    _scenario_agent_echo(),
    _scenario_stale_contradiction(),
    _scenario_clean_mcp_evidence(),
    _scenario_objective_conflict(),
    _scenario_partial_fulfillment(),
    _scenario_fefo_failure(),
    _scenario_capacity_conflict(),
    _scenario_duplicate_retry(),
)
