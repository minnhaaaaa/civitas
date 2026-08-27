"""Durable workflow-event and Server-Sent Event payload contracts."""

from datetime import datetime

from pydantic import Field

from civitas.contracts.common import Contract, JsonObject
from civitas.contracts.enums import WorkflowEventType


class WorkflowEvent(Contract):
    event_id: str
    planning_run_id: str
    sequence: int = Field(ge=0)
    event_type: WorkflowEventType
    occurred_at: datetime
    actor_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    schema_version: str = "1"
    payload: JsonObject = Field(default_factory=dict)


class SSEPayload(Contract):
    id: str
    event: WorkflowEventType
    data: WorkflowEvent
    retry_milliseconds: int | None = Field(default=None, ge=0)
