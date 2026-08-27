"""Decision Integrity v1 calculation and non-negotiable Jury gates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from civitas.contracts.claims import TypedClaim
from civitas.contracts.enums import FeasibilityStatus, JuryState
from civitas.contracts.evidence import EvidenceRecord
from civitas.contracts.jury import (
    IntegrityComponents,
    JuryEvaluation,
    JuryGateResult,
    JuryRequest,
)
from civitas.evidence.claims import normalize_predicate
from civitas.evidence.contradictions import (
    Contradiction,
    ContradictionSeverity,
    detect_contradictions,
)
from civitas.evidence.dissent import DissentReport
from civitas.evidence.graph import EvidenceGraphProjector, LineageAnalyzer


class ReasonCode(StrEnum):
    SOLVER_INFEASIBLE = "SOLVER_INFEASIBLE"
    HARD_CONSTRAINT_VIOLATION = "HARD_CONSTRAINT_VIOLATION"
    CRITICAL_CONTRADICTION_UNRESOLVED = "CRITICAL_CONTRADICTION_UNRESOLVED"
    CRITICAL_CLAIM_UNSUPPORTED = "CRITICAL_CLAIM_UNSUPPORTED"
    STALE_EXECUTION_DATA = "STALE_EXECUTION_DATA"
    AUTONOMY_BUDGET_EXHAUSTED = "AUTONOMY_BUDGET_EXHAUSTED"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    STRONG_EVIDENCE_INVALIDATES_PLAN = "STRONG_EVIDENCE_INVALIDATES_PLAN"
    DISSENT_CHECK_FAILED = "DISSENT_CHECK_FAILED"
    INTEGRITY_BELOW_APPROVAL_THRESHOLD = "INTEGRITY_BELOW_APPROVAL_THRESHOLD"
    INTEGRITY_BELOW_INVESTIGATION_THRESHOLD = "INTEGRITY_BELOW_INVESTIGATION_THRESHOLD"


@dataclass(frozen=True, slots=True)
class IntegrityPolicyV1:
    version: str = "decision-integrity-v1"
    implementation_version: str = "evidence-jury-1.0.0"
    approval_threshold: float = 85.0
    investigation_threshold: float = 60.0
    required_independent_sources: int = 2
    required_source_types: int = 2
    freshness_ttls: Mapping[str, timedelta] = field(
        default_factory=lambda: {
            "inventory_balance": timedelta(minutes=2),
            "inventory_reservation": timedelta(minutes=2),
            "warehouse_capacity": timedelta(minutes=2),
            "supplier_availability": timedelta(minutes=10),
            "supplier_capacity": timedelta(minutes=10),
            "lead_time": timedelta(minutes=10),
            "delivery_window": timedelta(minutes=10),
            "unit_price": timedelta(minutes=10),
            "transport_capacity": timedelta(minutes=10),
            "demand_forecast": timedelta(hours=6),
            "shelf_life": timedelta(hours=24),
            "organization_policy": timedelta(0),
        }
    )
    default_freshness_ttl: timedelta = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class GateFacts:
    hard_constraint_violations: tuple[str, ...] = ()
    stale_execution_claim_ids: tuple[str, ...] = ()
    human_approval_required: bool = False
    unresolved_uncertainty: bool = False


@dataclass(frozen=True, slots=True)
class JuryInputs:
    claims: tuple[TypedClaim, ...]
    evidence: tuple[EvidenceRecord, ...]
    dissent: DissentReport | None
    critical_claim_ids: frozenset[str] | None = None
    resolved_contradiction_ids: frozenset[str] = frozenset()
    gate_facts: GateFacts = GateFacts()


@dataclass(frozen=True, slots=True)
class IntegrityCalculation:
    components: IntegrityComponents
    score: float
    contradictions: tuple[Contradiction, ...]
    unsupported_critical_claim_ids: tuple[str, ...]
    stale_claim_ids: tuple[str, ...]


_WEIGHTS = {
    "critical_claim_coverage": 0.20,
    "evidence_independence": 0.20,
    "provenance_completeness": 0.15,
    "evidence_freshness": 0.15,
    "canonical_source_diversity": 0.10,
    "contradiction_resolution": 0.10,
    "dissent_robustness": 0.10,
}


class DecisionIntegrityCalculator:
    def __init__(self, policy: IntegrityPolicyV1 | None = None) -> None:
        self.policy = policy or IntegrityPolicyV1()

    def calculate(
        self,
        inputs: JuryInputs,
        *,
        calculated_at: datetime,
        default_critical_claim_ids: Sequence[str] = (),
    ) -> IntegrityCalculation:
        critical_ids = (
            inputs.critical_claim_ids
            if inputs.critical_claim_ids is not None
            else frozenset(default_critical_claim_ids)
        )
        claims_by_id = {claim.claim_id: claim for claim in inputs.claims}
        graph = EvidenceGraphProjector().project(inputs.claims, inputs.evidence)
        lineage = LineageAnalyzer(graph)

        source_groups = {
            claim_id: (
                lineage.claim_source_groups(claim_id) if claim_id in claims_by_id else frozenset()
            )
            for claim_id in critical_ids
        }
        unsupported = tuple(
            sorted(claim_id for claim_id, groups in source_groups.items() if not groups)
        )
        coverage = _percentage(len(critical_ids) - len(unsupported), len(critical_ids))

        independence_values = [
            min(1.0, len(groups) / self.policy.required_independent_sources) * 100
            for groups in source_groups.values()
        ]
        independence = _average(independence_values, empty=100.0)

        relevant_evidence = _relevant_evidence(inputs.evidence, critical_ids, lineage)
        provenance_values = [
            _provenance_score(record, lineage.has_complete_lineage(record.evidence_id))
            for record in relevant_evidence
        ]
        provenance = _average(provenance_values, empty=0.0 if critical_ids else 100.0)

        freshness_values: list[float] = []
        stale_claim_ids: set[str] = set()
        for claim_id in critical_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                freshness_values.append(0.0)
                stale_claim_ids.add(claim_id)
                continue
            supporting = [
                record
                for record in relevant_evidence
                if claim_id in record.claim_ids
                and lineage.effective_source_groups(record.evidence_id)
            ]
            ttl = self.policy.freshness_ttls.get(
                normalize_predicate(claim.predicate), self.policy.default_freshness_ttl
            )
            fresh = any(_is_fresh(record, calculated_at, ttl) for record in supporting)
            freshness_values.append(100.0 if fresh else 0.0)
            if not fresh:
                stale_claim_ids.add(claim_id)
        freshness = _average(freshness_values, empty=100.0)

        all_groups = set().union(*source_groups.values()) if source_groups else set()
        source_types = {group.split(":", maxsplit=1)[0] for group in all_groups}
        diversity = min(1.0, len(source_types) / self.policy.required_source_types) * 100
        if not critical_ids:
            diversity = 100.0

        contradictions = detect_contradictions(inputs.claims, critical_claim_ids=critical_ids)
        unresolved = [
            item
            for item in contradictions
            if item.contradiction_id not in inputs.resolved_contradiction_ids
        ]
        contradiction_score = _percentage(
            len(contradictions) - len(unresolved), len(contradictions)
        )
        dissent_score = inputs.dissent.robustness_score if inputs.dissent is not None else 0.0

        components = IntegrityComponents(
            critical_claim_coverage=coverage,
            evidence_independence=independence,
            provenance_completeness=provenance,
            evidence_freshness=freshness,
            canonical_source_diversity=diversity,
            contradiction_resolution=contradiction_score,
            dissent_robustness=dissent_score,
        )
        score = round(
            sum(getattr(components, name) * weight for name, weight in _WEIGHTS.items()), 2
        )
        return IntegrityCalculation(
            components=components,
            score=score,
            contradictions=contradictions,
            unsupported_critical_claim_ids=unsupported,
            stale_claim_ids=tuple(sorted(stale_claim_ids)),
        )


class JuryEvaluator:
    """Evaluate a solver plan with deterministic scoring and fail-closed gates."""

    def __init__(self, policy: IntegrityPolicyV1 | None = None) -> None:
        self.policy = policy or IntegrityPolicyV1()
        self._calculator = DecisionIntegrityCalculator(self.policy)

    def evaluate(
        self,
        request: JuryRequest,
        inputs: JuryInputs,
        *,
        evaluation_id: str,
        calculated_at: datetime,
    ) -> JuryEvaluation:
        if request.policy_version != self.policy.version:
            raise ValueError(
                f"request policy {request.policy_version!r} does not match evaluator "
                f"policy {self.policy.version!r}"
            )
        if request.candidate_plan.planning_run_id != request.planning_run_id:
            raise ValueError("candidate plan and Jury request must belong to the same planning run")
        scoped_inputs = replace(
            inputs,
            evidence=tuple(
                record for record in inputs.evidence if record.evidence_id in request.evidence_ids
            ),
        )
        calculation = self._calculator.calculate(
            scoped_inputs,
            calculated_at=calculated_at,
            default_critical_claim_ids=request.supporting_claim_ids,
        )
        gates = self._evaluate_gates(request, scoped_inputs, calculation)
        state, threshold_reason = self._state(request, scoped_inputs, calculation, gates)
        reasons = _unique_reason_codes(gates, threshold_reason)
        return JuryEvaluation(
            evaluation_id=evaluation_id,
            planning_run_id=request.planning_run_id,
            plan_id=request.candidate_plan.plan_id,
            policy_version=self.policy.version,
            implementation_version=self.policy.implementation_version,
            calculated_at=calculated_at,
            components=calculation.components,
            integrity_score=calculation.score,
            gates=gates,
            state=state,
            reason_codes=reasons,
            required_investigation=required_investigation(reasons, calculation, scoped_inputs),
        )

    def _evaluate_gates(
        self,
        request: JuryRequest,
        inputs: JuryInputs,
        calculation: IntegrityCalculation,
    ) -> tuple[JuryGateResult, ...]:
        critical_ids = (
            inputs.critical_claim_ids
            if inputs.critical_claim_ids is not None
            else frozenset(request.supporting_claim_ids)
        )
        unresolved_high = any(
            item.severity == ContradictionSeverity.HIGH
            and item.contradiction_id not in inputs.resolved_contradiction_ids
            and ({item.left_claim_id, item.right_claim_id} & critical_ids)
            for item in calculation.contradictions
        )
        dissent_failed = inputs.dissent is None or not inputs.dissent.completed
        invalidated = bool(inputs.dissent and inputs.dissent.establishes_invalidity)
        facts = inputs.gate_facts
        return (
            _gate(
                "solver_feasibility",
                request.candidate_plan.feasibility != FeasibilityStatus.INFEASIBLE,
                ReasonCode.SOLVER_INFEASIBLE,
            ),
            _gate(
                "hard_constraints",
                not facts.hard_constraint_violations,
                ReasonCode.HARD_CONSTRAINT_VIOLATION,
            ),
            _gate(
                "critical_contradictions",
                not unresolved_high,
                ReasonCode.CRITICAL_CONTRADICTION_UNRESOLVED,
            ),
            _gate(
                "critical_external_support",
                not calculation.unsupported_critical_claim_ids,
                ReasonCode.CRITICAL_CLAIM_UNSUPPORTED,
            ),
            _gate(
                "execution_freshness",
                not facts.stale_execution_claim_ids,
                ReasonCode.STALE_EXECUTION_DATA,
            ),
            _gate(
                "autonomy_bounds",
                not (request.autonomy_budget_exhausted and facts.unresolved_uncertainty),
                ReasonCode.AUTONOMY_BUDGET_EXHAUSTED,
            ),
            _gate(
                "human_approval",
                not facts.human_approval_required,
                ReasonCode.HUMAN_APPROVAL_REQUIRED,
            ),
            _gate(
                "proposal_validity",
                not invalidated,
                ReasonCode.STRONG_EVIDENCE_INVALIDATES_PLAN,
            ),
            _gate("dissent_completion", not dissent_failed, ReasonCode.DISSENT_CHECK_FAILED),
        )

    def _state(
        self,
        request: JuryRequest,
        inputs: JuryInputs,
        calculation: IntegrityCalculation,
        gates: tuple[JuryGateResult, ...],
    ) -> tuple[JuryState, ReasonCode | None]:
        failed = {reason for gate in gates if not gate.passed for reason in gate.reason_codes}
        if {
            ReasonCode.SOLVER_INFEASIBLE,
            ReasonCode.HARD_CONSTRAINT_VIOLATION,
            ReasonCode.STRONG_EVIDENCE_INVALIDATES_PLAN,
        } & failed:
            return JuryState.REJECT, None
        if {ReasonCode.AUTONOMY_BUDGET_EXHAUSTED, ReasonCode.HUMAN_APPROVAL_REQUIRED} & failed:
            return JuryState.ESCALATE, None
        if failed:
            return JuryState.INVESTIGATE, None
        if calculation.score >= self.policy.approval_threshold:
            return JuryState.APPROVE, None
        if calculation.score >= self.policy.investigation_threshold:
            return JuryState.INVESTIGATE, ReasonCode.INTEGRITY_BELOW_APPROVAL_THRESHOLD
        if request.autonomy_budget_exhausted:
            return JuryState.ESCALATE, ReasonCode.AUTONOMY_BUDGET_EXHAUSTED
        return JuryState.INVESTIGATE, ReasonCode.INTEGRITY_BELOW_INVESTIGATION_THRESHOLD


def required_investigation(
    reasons: tuple[str, ...],
    calculation: IntegrityCalculation,
    inputs: JuryInputs,
) -> tuple[str, ...]:
    tasks: list[str] = []
    reason_set = set(reasons)
    if ReasonCode.CRITICAL_CLAIM_UNSUPPORTED in reason_set:
        tasks.extend(
            f"Obtain external support for critical claim {claim_id}."
            for claim_id in calculation.unsupported_critical_claim_ids
        )
    if ReasonCode.CRITICAL_CONTRADICTION_UNRESOLVED in reason_set:
        tasks.extend(
            f"Resolve contradiction {item.contradiction_id} using stronger, current evidence."
            for item in calculation.contradictions
            if item.contradiction_id not in inputs.resolved_contradiction_ids
        )
    if ReasonCode.STALE_EXECUTION_DATA in reason_set:
        tasks.extend(
            f"Refresh execution-critical claim {claim_id}."
            for claim_id in inputs.gate_facts.stale_execution_claim_ids
        )
    if ReasonCode.DISSENT_CHECK_FAILED in reason_set:
        tasks.append("Run all required Dissent checks in a fresh read-only clean-room context.")
    if ReasonCode.INTEGRITY_BELOW_APPROVAL_THRESHOLD in reason_set:
        tasks.append("Collect additional independent, fresh evidence for material claims.")
    return tuple(dict.fromkeys(tasks))


def _relevant_evidence(
    evidence: tuple[EvidenceRecord, ...],
    critical_ids: frozenset[str],
    lineage: LineageAnalyzer,
) -> tuple[EvidenceRecord, ...]:
    direct = {record.evidence_id for record in evidence if set(record.claim_ids) & critical_ids}
    relevant_ids = set(direct)
    for evidence_id in direct:
        relevant_ids.update(lineage.dependencies(evidence_id))
    return tuple(record for record in evidence if record.evidence_id in relevant_ids)


def _provenance_score(record: EvidenceRecord, complete_lineage: bool) -> float:
    identity = record.identity
    fields = (
        bool(identity.canonical_source_id.strip()),
        bool(identity.canonical_source_type.strip()),
        identity.mcp_server is not None and bool(identity.mcp_server.strip()),
        identity.tool_name is not None and bool(identity.tool_name.strip()),
        identity.retrieved_at.tzinfo is not None,
        len(identity.raw_response_sha256) == 64,
        complete_lineage,
    )
    return round(sum(fields) / len(fields) * 100, 2)


def _is_fresh(record: EvidenceRecord, calculated_at: datetime, ttl: timedelta) -> bool:
    retrieved_at = record.identity.retrieved_at
    if retrieved_at.tzinfo is None or calculated_at.tzinfo is None:
        return False
    if ttl == timedelta(0):
        # Exact versioned policy is represented by observation_version rather than wall-clock age.
        return bool(record.identity.observation_version)
    age = calculated_at - retrieved_at
    return timedelta(0) <= age <= ttl


def _percentage(numerator: int, denominator: int) -> float:
    return 100.0 if denominator == 0 else round(numerator / denominator * 100, 2)


def _average(values: Sequence[float], *, empty: float) -> float:
    return empty if not values else round(sum(values) / len(values), 2)


def _gate(code: str, passed: bool, reason: ReasonCode) -> JuryGateResult:
    return JuryGateResult(
        gate_code=code,
        passed=passed,
        reason_codes=() if passed else (reason.value,),
    )


def _unique_reason_codes(
    gates: tuple[JuryGateResult, ...], threshold_reason: ReasonCode | None
) -> tuple[str, ...]:
    reasons = [reason for gate in gates if not gate.passed for reason in gate.reason_codes]
    if threshold_reason is not None:
        reasons.append(threshold_reason.value)
    return tuple(dict.fromkeys(reasons))
