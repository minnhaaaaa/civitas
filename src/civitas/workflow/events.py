"""Typed workflow transition payloads and event creation."""

from __future__ import annotations

from datetime import datetime

from civitas.contracts import WorkflowEvent, WorkflowEventType
from civitas.contracts.common import Contract
from civitas.contracts.jury import JuryEvaluation
from civitas.workflow.models import WorkflowPhase


class RunStartedPayload(Contract):
    phase: WorkflowPhase
    cycle: int


class ProposalRoundPayload(Contract):
    phase: WorkflowPhase
    cycle: int
    proposal_count: int
    repeated_evidence_ids: tuple[str, ...] = ()


class ChallengeRoundPayload(Contract):
    phase: WorkflowPhase
    cycle: int
    challenge_count: int


class ConcessionRoundPayload(Contract):
    phase: WorkflowPhase
    cycle: int
    concession_count: int
    selected_plan_id: str


class JuryRoutedPayload(Contract):
    phase: WorkflowPhase
    cycle: int
    plan_id: str
    state: str


class InvestigationPayload(Contract):
    phase: WorkflowPhase
    cycle: int
    required_investigation: tuple[str, ...]


class TerminalPayload(Contract):
    phase: WorkflowPhase
    cycle: int
    final_state: str
    reason: str


def make_event(
    *,
    event_id: str,
    planning_run_id: str,
    sequence: int,
    event_type: WorkflowEventType,
    occurred_at: datetime,
    payload: Contract,
) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=event_id,
        planning_run_id=planning_run_id,
        sequence=sequence,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload.model_dump(mode="json"),
    )


def jury_payload(evaluation: JuryEvaluation, cycle: int) -> JuryRoutedPayload:
    return JuryRoutedPayload(
        phase=WorkflowPhase.JURY,
        cycle=cycle,
        plan_id=evaluation.plan_id,
        state=evaluation.state,
    )
