"""Workflow-local checkpoint and Parliament data models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from civitas.contracts.common import Contract, JsonObject
from civitas.contracts.jury import JuryEvaluation
from civitas.contracts.optimization import OptimizationRequest, OptimizationResult


class WorkflowPhase(StrEnum):
    PROPOSAL = "proposal"
    CHALLENGE = "challenge"
    CONCESSION = "concession"
    JURY = "jury"
    INVESTIGATION = "investigation"
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


class WorkflowLimits(Contract):
    max_cycles: int = Field(default=3, ge=1)
    max_tool_calls: int = Field(default=0, ge=0)
    max_cost: Decimal = Field(default=Decimal("0"), ge=0)
    max_repeated_evidence: int = Field(default=1, ge=0)
    deadline_at: datetime


class InvestigationOutcome(Contract):
    """Durable result of one bounded evidence-retrieval round."""

    optimization_request: OptimizationRequest
    completed_task_ids: tuple[str, ...] = ()
    unavailable_tasks: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    evidence_fingerprints: tuple[str, ...] = ()
    canonical_source_groups: tuple[str, ...] = ()
    tool_calls_used: int = Field(default=0, ge=0)
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)


class PlanAssessment(Contract):
    plan_id: str
    score: Decimal
    reasons: tuple[str, ...] = ()


class ParliamentProposal(Contract):
    role: str
    preferred_plan_id: str | None = None
    acceptable_plan_ids: tuple[str, ...] = ()
    assessments: tuple[PlanAssessment, ...] = ()
    supporting_claim_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    reasoning_summary: str


class ParliamentChallenge(Contract):
    role: str
    target_role: str
    target_plan_id: str | None = None
    reason: str
    blocking: bool = True


class ParliamentConcession(Contract):
    role: str
    from_plan_id: str
    to_plan_id: str
    reason: str


class ParliamentSession(Contract):
    proposals: tuple[ParliamentProposal, ...] = ()
    challenges: tuple[ParliamentChallenge, ...] = ()
    concessions: tuple[ParliamentConcession, ...] = ()
    selected_plan_id: str | None = None
    repeated_evidence_ids: tuple[str, ...] = ()


class ParliamentContext(Contract):
    cycle: int = Field(ge=1)
    optimization_request: OptimizationRequest
    optimization_result: OptimizationResult
    prior_investigations: tuple[str, ...] = ()
    plan_annotations: JsonObject = Field(default_factory=dict)


class WorkflowCheckpoint(Contract):
    planning_run_id: str
    phase: WorkflowPhase
    cycle: int = Field(ge=1)
    event_sequence: int = Field(default=0, ge=0)
    optimization_request: OptimizationRequest
    optimization_result: OptimizationResult | None = None
    parliament: ParliamentSession | None = None
    jury_evaluation: JuryEvaluation | None = None
    seen_evidence_ids: tuple[str, ...] = ()
    seen_evidence_fingerprints: tuple[str, ...] = ()
    seen_canonical_source_groups: tuple[str, ...] = ()
    repeated_evidence_hits: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)
    estimated_cost_used: Decimal = Field(default=Decimal("0"), ge=0)
    investigation_backlog: tuple[str, ...] = ()
    completed_investigation_tasks: tuple[str, ...] = ()
    unavailable_investigation_tasks: tuple[str, ...] = ()
    final_state: str | None = None
    completed: bool = False


class WorkflowResult(Contract):
    checkpoint: WorkflowCheckpoint
    events: tuple[dict[str, object], ...]
