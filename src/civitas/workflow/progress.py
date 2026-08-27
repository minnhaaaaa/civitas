"""Compact, monotonic workflow progress projections for polling clients."""

from __future__ import annotations

from pydantic import Field

from civitas.contracts.common import Contract, JsonObject
from civitas.contracts.workflow import WorkflowEvent
from civitas.workflow.checkpointing import WorkflowCheckpointStore


class WorkflowProgressEvent(Contract):
    sequence: int = Field(ge=1)
    event_type: str
    phase: str | None = None
    cycle: int | None = Field(default=None, ge=1)
    summary: str


class WorkflowProgressPage(Contract):
    planning_run_id: str
    events: tuple[WorkflowProgressEvent, ...]
    next_cursor: int = Field(ge=0)
    complete: bool


class WorkflowProgressReader:
    def __init__(self, store: WorkflowCheckpointStore) -> None:
        self._store = store

    async def page(
        self, *, planning_run_id: str, cursor: int = 0, limit: int = 50
    ) -> WorkflowProgressPage:
        if cursor < 0 or not 1 <= limit <= 100:
            raise ValueError("cursor must be non-negative and limit must be between 1 and 100")
        events = await self._store.list_events(
            planning_run_id=planning_run_id, after_sequence=cursor, limit=limit
        )
        checkpoint = await self._store.get_checkpoint(planning_run_id)
        if checkpoint is None:
            raise KeyError("planning run not found")
        projected = tuple(_project(event) for event in events)
        next_cursor = projected[-1].sequence if projected else cursor
        return WorkflowProgressPage(
            planning_run_id=planning_run_id,
            events=projected,
            next_cursor=next_cursor,
            complete=checkpoint.completed,
        )


def _project(event: WorkflowEvent) -> WorkflowProgressEvent:
    payload: JsonObject = event.payload
    phase = payload.get("phase")
    cycle = payload.get("cycle")
    return WorkflowProgressEvent(
        sequence=event.sequence,
        event_type=event.event_type.value,
        phase=phase if isinstance(phase, str) else None,
        cycle=cycle if isinstance(cycle, int) and not isinstance(cycle, bool) else None,
        summary=_summary(event),
    )


def _summary(event: WorkflowEvent) -> str:
    payload = event.payload
    if event.event_type.value == "run.completed":
        return f"Run {payload.get('final_state', 'completed')}: {payload.get('reason', '')}".strip()
    if event.event_type.value == "investigation.requested":
        return "Investigation requested."
    if event.event_type.value == "jury.evaluated":
        return f"Jury routed decision to {payload.get('state', 'review')}."
    return event.event_type.value.replace(".", " ").capitalize() + "."
