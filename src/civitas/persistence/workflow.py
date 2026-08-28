"""PostgreSQL-backed workflow queue, leases, checkpoints, and progress events."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civitas.contracts.enums import WorkflowEventType
from civitas.contracts.workflow import WorkflowEvent
from civitas.persistence.models import (
    PlanningRunModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
)
from civitas.workflow.checkpointing import CheckpointConflictError, WorkflowLease
from civitas.workflow.models import WorkflowCheckpoint, WorkflowLimits


class PostgreSQLWorkflowCheckpointStore:
    """Atomic PostgreSQL implementation of the durable workflow-store port.

    A transition owns a row only while its opaque lease token is current. The
    checkpoint update and its contiguous progress events commit together.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        token_factory: Callable[[], str] | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._now_factory = now_factory or _aware_now

    async def enqueue(
        self, checkpoint: WorkflowCheckpoint, *, limits: WorkflowLimits | None = None
    ) -> None:
        now = self._now_factory()
        _require_aware(now, "now_factory result")
        row = WorkflowCheckpointModel(
            planning_run_id=checkpoint.planning_run_id,
            checkpoint=checkpoint.model_dump(mode="json"),
            workflow_limits=None if limits is None else limits.model_dump(mode="json"),
            phase=checkpoint.phase.value,
            cycle=checkpoint.cycle,
            event_sequence=checkpoint.event_sequence,
            completed=checkpoint.completed,
            available_at=now,
            updated_at=now,
        )
        try:
            async with self._sessions() as session, session.begin():
                session.add(row)
        except IntegrityError as exc:
            raise CheckpointConflictError("planning run already exists or is invalid") from exc

    async def claim(
        self, *, worker_id: str, now: datetime, lease_for: timedelta
    ) -> WorkflowLease | None:
        _validate_claim(worker_id=worker_id, now=now, lease_for=lease_for)
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(WorkflowCheckpointModel)
                .where(
                    WorkflowCheckpointModel.completed.is_(False),
                    WorkflowCheckpointModel.available_at <= now,
                    or_(
                        WorkflowCheckpointModel.lease_token.is_(None),
                        WorkflowCheckpointModel.lease_expires_at <= now,
                    ),
                )
                .order_by(
                    WorkflowCheckpointModel.available_at,
                    WorkflowCheckpointModel.created_at,
                    WorkflowCheckpointModel.planning_run_id,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            token = self._token_factory()
            if not token:
                raise ValueError("token_factory returned an empty lease token")
            expires_at = now + lease_for
            row.lease_owner = worker_id
            row.lease_token = token
            row.lease_expires_at = expires_at
            row.last_claimed_at = now
            row.attempt_count += 1
            row.updated_at = now
            checkpoint = WorkflowCheckpoint.model_validate(row.checkpoint)
            limits = (
                None
                if row.workflow_limits is None
                else WorkflowLimits.model_validate(row.workflow_limits)
            )
            return WorkflowLease(
                planning_run_id=row.planning_run_id,
                worker_id=worker_id,
                token=token,
                checkpoint=checkpoint,
                expires_at=expires_at,
                limits=limits,
            )

    async def commit_transition(
        self,
        *,
        lease: WorkflowLease,
        checkpoint: WorkflowCheckpoint,
        events: Sequence[WorkflowEvent],
        now: datetime,
    ) -> None:
        _validate_transition(lease=lease, checkpoint=checkpoint, events=events, now=now)
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(WorkflowCheckpointModel)
                .where(WorkflowCheckpointModel.planning_run_id == lease.planning_run_id)
                .with_for_update()
            )
            if not _owns_valid_lease(row, lease, now):
                raise CheckpointConflictError("workflow lease is no longer valid")
            assert row is not None
            _validate_cursor(
                current_sequence=row.event_sequence,
                checkpoint=checkpoint,
                events=events,
            )
            snapshot = checkpoint.model_dump(mode="json")
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
                        payload={"event": event.payload, "checkpoint": snapshot},
                    )
                )
            row.checkpoint = snapshot
            row.phase = checkpoint.phase.value
            row.cycle = checkpoint.cycle
            row.event_sequence = checkpoint.event_sequence
            row.completed = checkpoint.completed
            row.available_at = now
            row.lease_owner = None
            row.lease_token = None
            row.lease_expires_at = None
            row.updated_at = now
            await session.execute(
                update(PlanningRunModel)
                .where(PlanningRunModel.id == checkpoint.planning_run_id)
                .values(status=checkpoint.final_state or checkpoint.phase.value)
            )

    async def release(self, lease: WorkflowLease) -> None:
        now = self._now_factory()
        _require_aware(now, "now_factory result")
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(WorkflowCheckpointModel)
                .where(
                    WorkflowCheckpointModel.planning_run_id == lease.planning_run_id,
                    WorkflowCheckpointModel.lease_owner == lease.worker_id,
                    WorkflowCheckpointModel.lease_token == lease.token,
                )
                .values(
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    available_at=now,
                    updated_at=now,
                )
            )

    async def recover_abandoned(self, *, now: datetime) -> int:
        _require_aware(now, "now")
        async with self._sessions() as session, session.begin():
            rows = (
                await session.scalars(
                    select(WorkflowCheckpointModel)
                    .where(
                        WorkflowCheckpointModel.completed.is_(False),
                        WorkflowCheckpointModel.lease_token.is_not(None),
                        WorkflowCheckpointModel.lease_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                row.lease_owner = None
                row.lease_token = None
                row.lease_expires_at = None
                row.available_at = now
                row.updated_at = now
            return len(rows)

    async def get_checkpoint(self, planning_run_id: str) -> WorkflowCheckpoint | None:
        async with self._sessions() as session:
            payload = await session.scalar(
                select(WorkflowCheckpointModel.checkpoint).where(
                    WorkflowCheckpointModel.planning_run_id == planning_run_id
                )
            )
        return None if payload is None else WorkflowCheckpoint.model_validate(payload)

    async def list_events(
        self, *, planning_run_id: str, after_sequence: int, limit: int
    ) -> tuple[WorkflowEvent, ...]:
        if after_sequence < 0 or limit < 1:
            raise ValueError("cursor must be non-negative and limit must be positive")
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(WorkflowEventModel)
                    .where(
                        WorkflowEventModel.planning_run_id == planning_run_id,
                        WorkflowEventModel.sequence > after_sequence,
                    )
                    .order_by(WorkflowEventModel.sequence)
                    .limit(limit)
                )
            ).all()
        return tuple(_event_from_row(row) for row in rows)


def _validate_claim(*, worker_id: str, now: datetime, lease_for: timedelta) -> None:
    if not worker_id.strip() or len(worker_id) > 128:
        raise ValueError("worker_id must contain between 1 and 128 characters")
    _require_aware(now, "now")
    if lease_for <= timedelta(0):
        raise ValueError("lease_for must be positive")


def _validate_transition(
    *,
    lease: WorkflowLease,
    checkpoint: WorkflowCheckpoint,
    events: Sequence[WorkflowEvent],
    now: datetime,
) -> None:
    _require_aware(now, "now")
    if checkpoint.planning_run_id != lease.planning_run_id:
        raise CheckpointConflictError("checkpoint belongs to a different run")
    for event in events:
        if event.planning_run_id != lease.planning_run_id:
            raise CheckpointConflictError("event belongs to a different run")


def _validate_cursor(
    *, current_sequence: int, checkpoint: WorkflowCheckpoint, events: Sequence[WorkflowEvent]
) -> None:
    if checkpoint.event_sequence < current_sequence:
        raise CheckpointConflictError("checkpoint regressed")
    expected = current_sequence + 1
    for event in events:
        if event.sequence != expected:
            raise CheckpointConflictError("events must be contiguous and run-scoped")
        expected += 1
    if checkpoint.event_sequence != expected - 1:
        raise CheckpointConflictError("checkpoint and event cursor disagree")


def _owns_valid_lease(
    row: WorkflowCheckpointModel | None, lease: WorkflowLease, now: datetime
) -> bool:
    return bool(
        row is not None
        and row.lease_owner == lease.worker_id
        and row.lease_token == lease.token
        and row.lease_expires_at is not None
        and row.lease_expires_at > now
    )


def _event_from_row(row: WorkflowEventModel) -> WorkflowEvent:
    nested = row.payload.get("event")
    payload = nested if isinstance(nested, dict) else row.payload
    return WorkflowEvent(
        event_id=row.id,
        planning_run_id=row.planning_run_id,
        sequence=row.sequence,
        event_type=WorkflowEventType(row.event_type),
        occurred_at=row.occurred_at,
        actor_id=row.actor_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        schema_version=row.schema_version,
        payload=payload,
    )


def _aware_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
