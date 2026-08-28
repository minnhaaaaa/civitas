"""Bounded read-only contracts for the optional signed audit viewer."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from civitas.contracts.common import Contract
from civitas.contracts.enums import JuryState


class AuditJuryGate(Contract):
    gate_code: str = Field(min_length=1, max_length=128)
    passed: bool
    reason_codes: tuple[str, ...] = Field(default=(), max_length=50)


class AuditJuryCycle(Contract):
    cycle: int = Field(ge=1)
    state: JuryState
    integrity_score: float = Field(ge=0, le=100)
    components: dict[str, float] = Field(default_factory=dict, max_length=20)
    gates: tuple[AuditJuryGate, ...] = Field(default=(), max_length=50)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=50)


class AuditExecutionSummary(Contract):
    approved_plan_id: str = Field(min_length=1, max_length=128)
    current_state: str = Field(min_length=1, max_length=32)
    detail: str = Field(min_length=1, max_length=500)
    event_count: int = Field(ge=0)


class AuditManifest(Contract):
    run_id: str = Field(min_length=1, max_length=128)
    selected_plan_id: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1_000)
    captured_at: datetime
    link_expires_at: datetime
    maximum_event_sequence: int = Field(ge=0)
    jury: tuple[AuditJuryCycle, ...] = Field(default=(), max_length=20)
    execution: AuditExecutionSummary


class AuditEventItem(Contract):
    event_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=64)
    occurred_at: datetime
    phase: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=50)


class AuditClaimReference(Contract):
    claim_id: str = Field(min_length=1, max_length=128)
    human_summary: str = Field(min_length=1, max_length=1_000)
    predicate: str = Field(min_length=1, max_length=128)
    materiality: str = Field(min_length=1, max_length=32)


class AuditEvidenceItem(Contract):
    evidence_id: str = Field(min_length=1, max_length=128)
    content_summary: str = Field(min_length=1, max_length=2_000)
    origin: Literal["external", "agent_derived"]
    source_group: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=64)
    retrieved_at: datetime
    observation_version: str | None = Field(default=None, max_length=128)
    claims: tuple[AuditClaimReference, ...] = Field(default=(), max_length=100)
    derived_from: tuple[str, ...] = Field(default=(), max_length=100)


class AuditExecutionEventItem(Contract):
    sequence: int = Field(ge=1)
    occurred_at: datetime
    state: str = Field(min_length=1, max_length=32)
    reason_code: str | None = Field(default=None, max_length=128)
    detail: str | None = Field(default=None, max_length=500)


class AuditEventPage(Contract):
    items: tuple[AuditEventItem, ...] = Field(default=(), max_length=100)
    next_cursor: str | None = Field(default=None, min_length=20, max_length=512)


class AuditEvidencePage(Contract):
    items: tuple[AuditEvidenceItem, ...] = Field(default=(), max_length=100)
    next_cursor: str | None = Field(default=None, min_length=20, max_length=512)


class AuditExecutionEventPage(Contract):
    items: tuple[AuditExecutionEventItem, ...] = Field(default=(), max_length=100)
    next_cursor: str | None = Field(default=None, min_length=20, max_length=512)
