"""Workflow control API and SSE adapters for guarded execution."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civitas.contracts.common import Contract
from civitas.contracts.enums import WorkflowEventType
from civitas.contracts.execution import ExecutionRequest
from civitas.contracts.optimization import OptimizationRequest
from civitas.contracts.workflow import WorkflowEvent
from civitas.execution.guarded import GuardedExecutionOutcome
from civitas.persistence.models import PlanningRunModel, WorkflowEventModel
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator
from civitas.workflow import ParliamentWorkflow, WorkflowCheckpoint, WorkflowLimits
from civitas.workflow.events import RunStartedPayload, make_event


class RunMode(StrEnum):
    INITIALIZE_ONLY = "initialize_only"
    TO_COMPLETION = "to_completion"


class StartWorkflowRequest(Contract):
    planning_run_id: str | None = None
    optimization_request: OptimizationRequest
    limits: WorkflowLimits
    mode: RunMode = RunMode.TO_COMPLETION


class ResumeWorkflowRequest(Contract):
    limits: WorkflowLimits
    mode: RunMode = RunMode.TO_COMPLETION


class ApprovePlanRequest(Contract):
    execution_id: str
    approved_plan_id: str
    jury_evaluation_id: str
    idempotency_key: str
    approval_policy_version: str
    action: dict[str, Any] = Field(default_factory=dict)


class WorkflowStateResponse(Contract):
    checkpoint: dict[str, Any]
    events: tuple[dict[str, Any], ...]


class WorkflowStore:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        ids: IDGenerator,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._ids = ids
        self._clock = clock

    async def initialize(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        event = make_event(
            event_id=self._ids.new_id("event"),
            planning_run_id=checkpoint.planning_run_id,
            sequence=1,
            event_type=WorkflowEventType.RUN_STARTED,
            occurred_at=self._clock.now(),
            payload=RunStartedPayload(phase=checkpoint.phase, cycle=checkpoint.cycle),
        )
        updated = checkpoint.model_copy(update={"event_sequence": 1})
        await self.append(updated, (event,))
        return updated

    async def append(
        self,
        checkpoint: WorkflowCheckpoint,
        events: tuple[WorkflowEvent, ...],
    ) -> None:
        async with self._sessions() as session:
            async with session.begin():
                for event in events:
                    session.add(
                        WorkflowEventModel(
                            id=event.event_id,
                            planning_run_id=event.planning_run_id,
                            sequence=event.sequence,
                            event_type=event.event_type.value,
                            occurred_at=event.occurred_at,
                            actor_id=event.actor_id,
                            correlation_id=event.correlation_id,
                            causation_id=event.causation_id,
                            schema_version=event.schema_version,
                            payload={
                                "event": event.payload,
                                "checkpoint": checkpoint.model_dump(mode="json"),
                            },
                        )
                    )
                await session.execute(
                    update(PlanningRunModel)
                    .where(PlanningRunModel.id == checkpoint.planning_run_id)
                    .values(status=checkpoint.final_state or checkpoint.phase.value)
                )

    async def latest_checkpoint(self, planning_run_id: str) -> WorkflowCheckpoint | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(WorkflowEventModel)
                .where(WorkflowEventModel.planning_run_id == planning_run_id)
                .order_by(WorkflowEventModel.sequence.desc())
                .limit(1)
            )
        if row is None:
            return None
        snapshot = row.payload.get("checkpoint")
        if not isinstance(snapshot, dict):
            return None
        return WorkflowCheckpoint.model_validate(snapshot)

    async def events(
        self,
        planning_run_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[WorkflowEvent, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(WorkflowEventModel)
                    .where(
                        WorkflowEventModel.planning_run_id == planning_run_id,
                        WorkflowEventModel.sequence > after_sequence,
                    )
                    .order_by(WorkflowEventModel.sequence)
                )
            ).all()
        parsed: list[WorkflowEvent] = []
        for row in rows:
            payload = row.payload.get("event")
            parsed.append(
                WorkflowEvent(
                    event_id=row.id,
                    planning_run_id=row.planning_run_id,
                    sequence=row.sequence,
                    event_type=WorkflowEventType(row.event_type),
                    occurred_at=row.occurred_at,
                    actor_id=row.actor_id,
                    correlation_id=row.correlation_id,
                    causation_id=row.causation_id,
                    schema_version=row.schema_version,
                    payload=payload if isinstance(payload, dict) else {},
                )
            )
        return tuple(parsed)


class WorkflowAPIService:
    def __init__(
        self,
        *,
        workflow: ParliamentWorkflow,
        store: WorkflowStore,
    ) -> None:
        self._workflow = workflow
        self._store = store

    async def start(self, request: StartWorkflowRequest) -> WorkflowCheckpoint:
        planning_run_id = request.planning_run_id
        if planning_run_id is None:
            raise ValueError("planning run id is required")
        if await self._store.latest_checkpoint(planning_run_id) is not None:
            raise ValueError("workflow already exists")
        checkpoint = self._workflow.start(
            planning_run_id=planning_run_id,
            optimization_request=request.optimization_request.model_copy(
                update={"planning_run_id": planning_run_id}
            ),
        )
        checkpoint = await self._store.initialize(checkpoint)
        return await self._drive(checkpoint, request.limits, request.mode)

    async def resume(
        self,
        planning_run_id: str,
        request: ResumeWorkflowRequest,
    ) -> WorkflowCheckpoint:
        checkpoint = await self._store.latest_checkpoint(planning_run_id)
        if checkpoint is None:
            raise ValueError("workflow not found")
        return await self._drive(checkpoint, request.limits, request.mode)

    async def inspect(self, planning_run_id: str) -> WorkflowStateResponse:
        checkpoint = await self._store.latest_checkpoint(planning_run_id)
        if checkpoint is None:
            raise ValueError("workflow not found")
        events = await self._store.events(planning_run_id)
        return WorkflowStateResponse(
            checkpoint=checkpoint.model_dump(mode="json"),
            events=tuple(event.model_dump(mode="json") for event in events),
        )

    async def stream_payloads(
        self,
        planning_run_id: str,
        *,
        cursor: int = 0,
    ) -> AsyncIterator[str]:
        for event in await self._store.events(planning_run_id, after_sequence=cursor):
            yield _to_sse(event)
            await asyncio.sleep(0)

    async def _drive(
        self,
        checkpoint: WorkflowCheckpoint,
        limits: WorkflowLimits,
        mode: RunMode,
    ) -> WorkflowCheckpoint:
        if checkpoint.completed or mode is RunMode.INITIALIZE_ONLY:
            return checkpoint
        while not checkpoint.completed:
            checkpoint, events = await self._workflow.advance(checkpoint, limits=limits)
            if events:
                await self._store.append(checkpoint, events)
            if mode is RunMode.INITIALIZE_ONLY:
                break
        return checkpoint


class ExecutionAPI(Protocol):
    async def execute(self, request: ExecutionRequest) -> GuardedExecutionOutcome: ...


def create_guarded_app(
    *,
    workflow_service: WorkflowAPIService,
    execution_service: ExecutionAPI,
) -> FastAPI:
    app = FastAPI(title="Civitas Guarded API")

    @app.post("/planning-runs/{planning_run_id}/start")
    async def start_workflow(
        planning_run_id: str,
        request: StartWorkflowRequest,
    ) -> JSONResponse:
        try:
            checkpoint = await workflow_service.start(
                request.model_copy(update={"planning_run_id": planning_run_id})
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return JSONResponse(checkpoint.model_dump(mode="json"))

    @app.get("/planning-runs/{planning_run_id}")
    async def inspect_workflow(planning_run_id: str) -> JSONResponse:
        try:
            state = await workflow_service.inspect(planning_run_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return JSONResponse(state.model_dump(mode="json"))

    @app.post("/planning-runs/{planning_run_id}/resume")
    async def resume_workflow(
        planning_run_id: str,
        request: ResumeWorkflowRequest,
    ) -> JSONResponse:
        try:
            checkpoint = await workflow_service.resume(planning_run_id, request)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return JSONResponse(checkpoint.model_dump(mode="json"))

    @app.post("/planning-runs/{planning_run_id}/approve")
    async def approve_workflow(
        planning_run_id: str,
        request: ApprovePlanRequest,
    ) -> JSONResponse:
        outcome = await execution_service.execute(
            ExecutionRequest(
                execution_id=request.execution_id,
                planning_run_id=planning_run_id,
                approved_plan_id=request.approved_plan_id,
                jury_evaluation_id=request.jury_evaluation_id,
                idempotency_key=request.idempotency_key,
                approval_policy_version=request.approval_policy_version,
                requested_at=datetime.now().astimezone(),
                action=request.action,
            )
        )
        return JSONResponse(outcome.model_dump(mode="json"))

    @app.get("/planning-runs/{planning_run_id}/stream")
    async def stream_workflow(
        planning_run_id: str,
        cursor: int = Query(default=0, ge=0),
    ) -> StreamingResponse:
        return StreamingResponse(
            workflow_service.stream_payloads(planning_run_id, cursor=cursor),
            media_type="text/event-stream",
        )

    return app


def _to_sse(event: WorkflowEvent) -> str:
    return (
        f"id: {event.sequence}\n"
        f"event: {event.event_type.value}\n"
        f"data: {json.dumps(event.model_dump(mode='json'))}\n\n"
    )
