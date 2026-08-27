"""Deterministic Parliament role agents over solver-generated alternatives."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from civitas.contracts.optimization import CandidatePlan, OptimizationResult
from civitas.workflow.models import (
    ParliamentChallenge,
    ParliamentConcession,
    ParliamentContext,
    ParliamentProposal,
    PlanAssessment,
)


class ParliamentRole(StrEnum):
    DEMAND = "demand"
    COST = "cost"
    FRESHNESS = "freshness"
    LOGISTICS = "logistics"
    SUPPLIER = "supplier"
    WASTE = "waste"


@dataclass(frozen=True, slots=True)
class MetricPreference:
    name: str
    maximize: bool


def default_role_agents() -> tuple[RoleAgent, ...]:
    return (
        RoleAgent(
            role=ParliamentRole.DEMAND,
            preferences=(
                MetricPreference("fulfillment", True),
                MetricPreference("critical_shortage", False),
                MetricPreference("resilience", True),
            ),
        ),
        RoleAgent(
            role=ParliamentRole.COST,
            preferences=(
                MetricPreference("total_landed_cost", False),
                MetricPreference("holding_cost", False),
            ),
        ),
        RoleAgent(
            role=ParliamentRole.FRESHNESS,
            preferences=(
                MetricPreference("remaining_shelf_life", True),
                MetricPreference("spoilage_exposure", False),
                MetricPreference("expected_waste_value", False),
            ),
        ),
        RoleAgent(
            role=ParliamentRole.LOGISTICS,
            preferences=(
                MetricPreference("lateness", False),
                MetricPreference("redistribution_effort", False),
                MetricPreference("capacity_slack", True),
            ),
        ),
        RoleAgent(
            role=ParliamentRole.SUPPLIER,
            preferences=(
                MetricPreference("supplier_reliability", True),
                MetricPreference("supplier_concentration", False),
                MetricPreference("capacity_risk", False),
            ),
        ),
        RoleAgent(
            role=ParliamentRole.WASTE,
            preferences=(
                MetricPreference("expected_waste_value", False),
                MetricPreference("expired_quantity", False),
            ),
        ),
    )


class RoleAgent:
    """Ranks existing solver alternatives without inventing new quantities."""

    def __init__(
        self,
        *,
        role: ParliamentRole,
        preferences: Sequence[MetricPreference],
    ) -> None:
        self.role = role
        self._preferences = tuple(preferences)

    def propose(self, context: ParliamentContext) -> ParliamentProposal:
        assessments = tuple(self._assess(plan) for plan in context.optimization_result.alternatives)
        ordered = tuple(sorted(assessments, key=lambda item: (-item.score, item.plan_id)))
        preferred = ordered[0]
        acceptable = tuple(item.plan_id for item in ordered if item.score == preferred.score)
        annotations = context.plan_annotations.get(preferred.plan_id, {})
        if not isinstance(annotations, dict):
            annotations = {}
        return ParliamentProposal(
            role=self.role.value,
            preferred_plan_id=preferred.plan_id,
            acceptable_plan_ids=acceptable,
            assessments=ordered,
            supporting_claim_ids=tuple(_tuple_strings(annotations.get("claim_ids"))),
            evidence_ids=tuple(_tuple_strings(annotations.get("evidence_ids"))),
            reasoning_summary=(
                f"{self.role.value} prefers {preferred.plan_id} based on "
                f"{', '.join(pref.name for pref in self._preferences)}."
            ),
        )

    def challenge(
        self,
        context: ParliamentContext,
        proposals: Sequence[ParliamentProposal],
    ) -> tuple[ParliamentChallenge, ...]:
        own = next(item for item in proposals if item.role == self.role.value)
        challenges: list[ParliamentChallenge] = []
        for proposal in proposals:
            if (
                proposal.role == self.role.value
                or proposal.preferred_plan_id in own.acceptable_plan_ids
            ):
                continue
            challenges.append(
                ParliamentChallenge(
                    role=self.role.value,
                    target_role=proposal.role,
                    target_plan_id=proposal.preferred_plan_id,
                    reason=(
                        f"{proposal.preferred_plan_id} falls outside {self.role.value}'s "
                        "acceptable alternatives."
                    ),
                    blocking=True,
                )
            )
        return tuple(challenges)

    def concede(
        self,
        context: ParliamentContext,
        proposals: Sequence[ParliamentProposal],
        support_counts: Mapping[str, int],
    ) -> ParliamentConcession | None:
        own = next(item for item in proposals if item.role == self.role.value)
        if own.preferred_plan_id is None:
            return None
        ranked_support = sorted(
            support_counts.items(),
            key=lambda item: (-item[1], self._rank(own, item[0]), item[0]),
        )
        if not ranked_support:
            return None
        target_plan_id, _ = ranked_support[0]
        if target_plan_id == own.preferred_plan_id or target_plan_id not in own.acceptable_plan_ids:
            return None
        return ParliamentConcession(
            role=self.role.value,
            from_plan_id=own.preferred_plan_id,
            to_plan_id=target_plan_id,
            reason=f"{self.role.value} concedes to the strongest acceptable consensus candidate.",
        )

    def _assess(self, plan: CandidatePlan) -> PlanAssessment:
        score = Decimal("0")
        reasons: list[str] = []
        weight = Decimal(len(self._preferences)) or Decimal("1")
        for pref in self._preferences:
            raw = Decimal(plan.metrics.get(pref.name, Decimal("0")))
            direction = Decimal("1") if pref.maximize else Decimal("-1")
            score += direction * raw / weight
            reasons.append(f"{pref.name}={raw}")
        return PlanAssessment(
            plan_id=plan.plan_id,
            score=score,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _rank(proposal: ParliamentProposal, plan_id: str) -> int:
        for index, assessment in enumerate(proposal.assessments):
            if assessment.plan_id == plan_id:
                return index
        return len(proposal.assessments) + 1


def select_consensus_plan(
    optimization_result: OptimizationResult,
    proposals: Sequence[ParliamentProposal],
    concessions: Sequence[ParliamentConcession],
) -> str:
    plan_ids = {plan.plan_id for plan in optimization_result.alternatives}
    effective_votes = [
        proposal.preferred_plan_id for proposal in proposals if proposal.preferred_plan_id
    ]
    for concession in concessions:
        if concession.to_plan_id in plan_ids:
            effective_votes.append(concession.to_plan_id)
    counts = Counter(effective_votes)
    best_plan_id: str | None = None
    best_key: tuple[int, int, str] | None = None
    for plan in optimization_result.alternatives:
        rank_sum = 0
        for proposal in proposals:
            for rank, assessment in enumerate(proposal.assessments):
                if assessment.plan_id == plan.plan_id:
                    rank_sum += rank
                    break
            else:
                rank_sum += len(proposal.assessments)
        key = (counts.get(plan.plan_id, 0), -rank_sum, plan.plan_id)
        if best_key is None or key > best_key:
            best_key = key
            best_plan_id = plan.plan_id
    if best_plan_id is None:
        raise ValueError("no candidate plans available")
    return best_plan_id


def support_counts(
    proposals: Sequence[ParliamentProposal],
    concessions: Sequence[ParliamentConcession] = (),
) -> dict[str, int]:
    counts = Counter(
        proposal.preferred_plan_id
        for proposal in proposals
        if proposal.preferred_plan_id is not None
    )
    counts.update(concession.to_plan_id for concession in concessions)
    return dict(counts)


def _tuple_strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))
