"""Durable checkpoint-store port and deterministic in-memory implementation.

The production adapter can back this port with PostgreSQL row locks.  The worker
only commits a completed transition together with its events, so an interruption
before that commit can never expose a half-transition or skip event cursors.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from civitas.contracts.workflow import WorkflowEvent
from civitas.workflow.models import WorkflowCheckpoint, WorkflowLimits


class CheckpointConflictError(RuntimeError):
    """A worker attempted to commit a transition it no longer owns."""


@dataclass(frozen=True, slots=True)
class WorkflowLease:
    planning_run_id: str
    worker_id: str
    token: str
    checkpoint: WorkflowCheckpoint
    expires_at: datetime
    limits: WorkflowLimits | None = None
    attempt_count: int = 1


class WorkflowCheckpointStore(Protocol):
    async def enqueue(
        self, checkpoint: WorkflowCheckpoint, *, limits: WorkflowLimits | None = None
    ) -> None: ...

    async def claim(
        self, *, worker_id: str, now: datetime, lease_for: timedelta
    ) -> WorkflowLease | None: ...

    async def renew(
        self, *, lease: WorkflowLease, now: datetime, lease_for: timedelta
    ) -> WorkflowLease: ...

    async def commit_transition(
        self,
        *,
        lease: WorkflowLease,
        checkpoint: WorkflowCheckpoint,
        events: Sequence[WorkflowEvent],
        now: datetime,
    ) -> None: ...

    async def release(self, lease: WorkflowLease) -> None: ...

    async def recover_abandoned(self, *, now: datetime) -> int: ...

    async def get_checkpoint(self, planning_run_id: str) -> WorkflowCheckpoint | None: ...

    async def list_events(
        self, *, planning_run_id: str, after_sequence: int, limit: int
    ) -> tuple[WorkflowEvent, ...]: ...


@dataclass(slots=True)
class _StoredRun:
    checkpoint: WorkflowCheckpoint
    events: list[WorkflowEvent]
    limits: WorkflowLimits | None = None
    lease: WorkflowLease | None = None
    attempt_count: int = 0


class InMemoryWorkflowCheckpointStore:
    """Atomic fake store used by deterministic worker tests.

    It deliberately mirrors the important database invariants: one active
    lease per run, compare-and-swap transition commits, and strictly increasing
    event sequences.
    """

    def __init__(self) -> None:
        self._runs: dict[str, _StoredRun] = {}
        self._lock = asyncio.Lock()
        self._next_token = 0

    async def enqueue(
        self, checkpoint: WorkflowCheckpoint, *, limits: WorkflowLimits | None = None
    ) -> None:
        async with self._lock:
            if checkpoint.planning_run_id in self._runs:
                raise CheckpointConflictError("planning run already exists")
            self._runs[checkpoint.planning_run_id] = _StoredRun(checkpoint, [], limits)

    async def claim(
        self, *, worker_id: str, now: datetime, lease_for: timedelta
    ) -> WorkflowLease | None:
        async with self._lock:
            for run_id in sorted(self._runs):
                run = self._runs[run_id]
                if run.checkpoint.completed:
                    continue
                if run.lease is not None and run.lease.expires_at > now:
                    continue
                self._next_token += 1
                run.attempt_count += 1
                lease = WorkflowLease(
                    planning_run_id=run_id,
                    worker_id=worker_id,
                    token=f"lease-{self._next_token}",
                    checkpoint=run.checkpoint,
                    expires_at=now + lease_for,
                    limits=run.limits,
                    attempt_count=run.attempt_count,
                )
                run.lease = lease
                return lease
        return None

    async def renew(
        self, *, lease: WorkflowLease, now: datetime, lease_for: timedelta
    ) -> WorkflowLease:
        if lease_for <= timedelta(0):
            raise ValueError("lease_for must be positive")
        async with self._lock:
            run = self._runs.get(lease.planning_run_id)
            if not _owns_lease(run, lease, now):
                raise CheckpointConflictError("workflow lease is no longer valid")
            renewed = WorkflowLease(
                planning_run_id=lease.planning_run_id,
                worker_id=lease.worker_id,
                token=lease.token,
                checkpoint=lease.checkpoint,
                expires_at=now + lease_for,
                limits=lease.limits,
                attempt_count=lease.attempt_count,
            )
            assert run is not None
            run.lease = renewed
            return renewed

    async def commit_transition(
        self,
        *,
        lease: WorkflowLease,
        checkpoint: WorkflowCheckpoint,
        events: Sequence[WorkflowEvent],
        now: datetime,
    ) -> None:
        async with self._lock:
            run = self._runs.get(lease.planning_run_id)
            if not _owns_lease(run, lease, now):
                raise CheckpointConflictError("workflow lease is no longer valid")
            assert run is not None
            if checkpoint.planning_run_id != lease.planning_run_id:
                raise CheckpointConflictError("checkpoint belongs to a different run")
            if checkpoint.event_sequence < run.checkpoint.event_sequence:
                raise CheckpointConflictError("checkpoint regressed")
            expected = run.checkpoint.event_sequence + 1
            for event in events:
                if event.planning_run_id != lease.planning_run_id or event.sequence != expected:
                    raise CheckpointConflictError("events must be contiguous and run-scoped")
                expected += 1
            if checkpoint.event_sequence != expected - 1:
                raise CheckpointConflictError("checkpoint and event cursor disagree")
            run.checkpoint = checkpoint
            run.events.extend(events)
            run.lease = None

    async def release(self, lease: WorkflowLease) -> None:
        async with self._lock:
            run = self._runs.get(lease.planning_run_id)
            if run is not None and _same_lease(run.lease, lease):
                run.lease = None

    async def recover_abandoned(self, *, now: datetime) -> int:
        async with self._lock:
            recovered = 0
            for run in self._runs.values():
                if run.lease is not None and run.lease.expires_at <= now:
                    run.lease = None
                    recovered += 1
            return recovered

    async def get_checkpoint(self, planning_run_id: str) -> WorkflowCheckpoint | None:
        async with self._lock:
            run = self._runs.get(planning_run_id)
            return None if run is None else run.checkpoint

    async def list_events(
        self, *, planning_run_id: str, after_sequence: int, limit: int
    ) -> tuple[WorkflowEvent, ...]:
        if after_sequence < 0 or limit < 1:
            raise ValueError("cursor must be non-negative and limit must be positive")
        async with self._lock:
            run = self._runs.get(planning_run_id)
            if run is None:
                return ()
            return tuple(event for event in run.events if event.sequence > after_sequence)[:limit]


def _same_lease(current: WorkflowLease | None, supplied: WorkflowLease) -> bool:
    return bool(
        current is not None
        and current.worker_id == supplied.worker_id
        and current.token == supplied.token
    )


def _owns_lease(run: _StoredRun | None, supplied: WorkflowLease, now: datetime) -> bool:
    return bool(
        run is not None
        and _same_lease(run.lease, supplied)
        and run.lease is not None
        and run.lease.expires_at > now
    )
