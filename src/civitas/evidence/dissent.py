"""Clean-room, read-only Dissent protocol state and validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DissentPhase(StrEnum):
    PLAN_RECORDED = "plan_recorded"
    FRESH_RETRIEVAL_COMPLETE = "fresh_retrieval_complete"
    COMPARISON_COMPLETE = "comparison_complete"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DissentInvestigationPlan:
    context_id: str
    memory_namespace: str
    tool_cache_namespace: str
    checks: tuple[str, ...]
    tool_budget: int
    read_only: bool = True

    def __post_init__(self) -> None:
        if not self.context_id.strip():
            raise ValueError("a separate Dissent context is required")
        if not self.memory_namespace.strip() or not self.tool_cache_namespace.strip():
            raise ValueError("separate memory and tool-cache namespaces are required")
        if self.tool_budget < 1:
            raise ValueError("Dissent tool budget must be positive")
        if not self.read_only:
            raise ValueError("Dissent tool access must be read-only")


@dataclass(frozen=True, slots=True)
class DissentReport:
    plan: DissentInvestigationPlan
    phase: DissentPhase
    fresh_evidence_ids: tuple[str, ...] = ()
    checked_claim_ids: tuple[str, ...] = ()
    unavailable_checks: tuple[str, ...] = ()
    contradiction_ids: tuple[str, ...] = ()
    establishes_invalidity: bool = False
    failure_reason: str | None = None

    @property
    def completed(self) -> bool:
        return (
            self.phase == DissentPhase.COMPARISON_COMPLETE
            and not self.unavailable_checks
            and self.failure_reason is None
            and bool(self.plan.checks)
            and bool(self.fresh_evidence_ids)
            and bool(self.checked_claim_ids)
        )

    @property
    def robustness_score(self) -> float:
        """Required checks fail closed; a partial/bare assertion earns zero credit."""

        if not self.completed:
            return 0.0
        if self.establishes_invalidity:
            return 0.0
        return 100.0


class DissentProtocol:
    """Enforce phase ordering without binding the domain to an MCP provider."""

    @staticmethod
    def record_plan(plan: DissentInvestigationPlan) -> DissentReport:
        return DissentReport(plan=plan, phase=DissentPhase.PLAN_RECORDED)

    @staticmethod
    def record_fresh_retrieval(
        report: DissentReport,
        *,
        evidence_ids: tuple[str, ...],
        unavailable_checks: tuple[str, ...] = (),
    ) -> DissentReport:
        if report.phase != DissentPhase.PLAN_RECORDED:
            raise ValueError("Dissent must record its plan before fresh retrieval")
        return DissentReport(
            plan=report.plan,
            phase=DissentPhase.FRESH_RETRIEVAL_COMPLETE,
            fresh_evidence_ids=evidence_ids,
            unavailable_checks=unavailable_checks,
        )

    @staticmethod
    def compare_with_existing_graph(
        report: DissentReport,
        *,
        checked_claim_ids: tuple[str, ...],
        contradiction_ids: tuple[str, ...] = (),
        establishes_invalidity: bool = False,
    ) -> DissentReport:
        if report.phase != DissentPhase.FRESH_RETRIEVAL_COMPLETE:
            raise ValueError("existing evidence is revealed only after fresh retrieval")
        return DissentReport(
            plan=report.plan,
            phase=DissentPhase.COMPARISON_COMPLETE,
            fresh_evidence_ids=report.fresh_evidence_ids,
            checked_claim_ids=checked_claim_ids,
            unavailable_checks=report.unavailable_checks,
            contradiction_ids=contradiction_ids,
            establishes_invalidity=establishes_invalidity,
        )

    @staticmethod
    def fail(report: DissentReport, reason: str) -> DissentReport:
        return DissentReport(
            plan=report.plan,
            phase=DissentPhase.FAILED,
            fresh_evidence_ids=report.fresh_evidence_ids,
            unavailable_checks=report.unavailable_checks,
            failure_reason=reason,
        )
