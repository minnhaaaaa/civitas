import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.workflow.test_parliament_workflow import (
    FakeClock,
    FakeIDs,
    FakeJury,
    FakeOptimizer,
    _evaluation,
    _request,
    _result,
)

from civitas.worker import DurableWorkflowWorker
from civitas.workflow import ParliamentWorkflow, WorkflowLimits
from civitas.workflow.checkpointing import InMemoryWorkflowCheckpointStore, WorkflowLease
from civitas.workflow.models import WorkflowPhase
from civitas.workflow.progress import WorkflowProgressReader


@pytest.mark.asyncio
async def test_worker_resumes_in_small_durable_transitions_with_monotonic_progress() -> None:
    now = datetime(2026, 8, 27, tzinfo=UTC)
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result()]),
        jury=FakeJury([_evaluation("approve")]),
        ids=FakeIDs(),
        clock=FakeClock(now),
    )
    store = InMemoryWorkflowCheckpointStore()
    worker = DurableWorkflowWorker(
        worker_id="worker-a",
        workflow=workflow,
        store=store,
        clock=FakeClock(now),
        limits=WorkflowLimits(max_cycles=2, deadline_at=now + timedelta(hours=1)),
    )
    await worker.enqueue(workflow.start(planning_run_id="run-1", optimization_request=_request()))

    assert await worker.process_next() is True
    first_page = await WorkflowProgressReader(store).page(planning_run_id="run-1")
    assert [event.sequence for event in first_page.events] == [1, 2]
    assert first_page.complete is False

    while await worker.process_next():
        pass
    final_page = await WorkflowProgressReader(store).page(
        planning_run_id="run-1", cursor=first_page.next_cursor
    )
    checkpoint = await store.get_checkpoint("run-1")
    assert checkpoint is not None and checkpoint.phase is WorkflowPhase.APPROVE
    assert final_page.complete is True
    assert [event.sequence for event in final_page.events] == list(
        range(3, final_page.next_cursor + 1)
    )


@pytest.mark.asyncio
async def test_worker_uses_limits_persisted_with_each_run() -> None:
    now = datetime(2026, 8, 27, tzinfo=UTC)
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result()]),
        jury=FakeJury([_evaluation("approve")]),
        ids=FakeIDs(),
        clock=FakeClock(now),
    )
    store = InMemoryWorkflowCheckpointStore()
    worker = DurableWorkflowWorker(
        worker_id="worker-a",
        workflow=workflow,
        store=store,
        clock=FakeClock(now),
    )
    limits = WorkflowLimits(max_cycles=2, deadline_at=now + timedelta(hours=1))
    await worker.enqueue(
        workflow.start(planning_run_id="run-1", optimization_request=_request()),
        limits=limits,
    )

    assert await worker.process_next() is True
    checkpoint = await store.get_checkpoint("run-1")
    assert checkpoint is not None and checkpoint.phase is WorkflowPhase.CHALLENGE


class _CurrentClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _SlowWorkflow:
    async def advance(
        self, checkpoint: object, *, limits: WorkflowLimits
    ) -> tuple[object, tuple[object, ...]]:
        del limits
        await asyncio.sleep(0.04)
        return checkpoint, ()


class _FailingWorkflow:
    async def advance(self, checkpoint: object, *, limits: WorkflowLimits) -> None:
        del checkpoint, limits
        raise RuntimeError("deterministic transition failure")


class _CountingStore(InMemoryWorkflowCheckpointStore):
    def __init__(self) -> None:
        super().__init__()
        self.renewals = 0

    async def renew(
        self, *, lease: WorkflowLease, now: datetime, lease_for: timedelta
    ) -> WorkflowLease:
        self.renewals += 1
        return await super().renew(lease=lease, now=now, lease_for=lease_for)


@pytest.mark.asyncio
async def test_worker_renews_lease_during_a_slow_transition() -> None:
    now = datetime.now(UTC)
    store = _CountingStore()
    checkpoint = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result()]),
        jury=FakeJury([_evaluation("approve")]),
        ids=FakeIDs(),
        clock=FakeClock(now),
    ).start(planning_run_id="run-slow", optimization_request=_request())
    await store.enqueue(
        checkpoint,
        limits=WorkflowLimits(max_cycles=2, deadline_at=now + timedelta(hours=1)),
    )
    worker = DurableWorkflowWorker(
        worker_id="worker-slow",
        workflow=_SlowWorkflow(),  # type: ignore[arg-type]
        store=store,
        clock=_CurrentClock(),
        lease_for=timedelta(milliseconds=30),
        heartbeat_interval=timedelta(milliseconds=5),
    )

    assert await worker.process_next() is True
    assert store.renewals >= 1


@pytest.mark.asyncio
async def test_repeated_worker_failure_escalates_with_monotonic_event() -> None:
    now = datetime(2026, 8, 27, tzinfo=UTC)
    store = InMemoryWorkflowCheckpointStore()
    checkpoint = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result()]),
        jury=FakeJury([_evaluation("approve")]),
        ids=FakeIDs(),
        clock=FakeClock(now),
    ).start(planning_run_id="run-failing", optimization_request=_request())
    await store.enqueue(
        checkpoint,
        limits=WorkflowLimits(max_cycles=2, deadline_at=now + timedelta(hours=1)),
    )
    worker = DurableWorkflowWorker(
        worker_id="worker-failing",
        workflow=_FailingWorkflow(),  # type: ignore[arg-type]
        store=store,
        clock=FakeClock(now),
        max_attempts=2,
    )

    with pytest.raises(RuntimeError, match="deterministic transition failure"):
        await worker.process_next()
    assert await worker.process_next() is True

    terminal = await store.get_checkpoint("run-failing")
    events = await store.list_events(planning_run_id="run-failing", after_sequence=0, limit=10)
    assert terminal is not None
    assert terminal.phase is WorkflowPhase.ESCALATE
    assert terminal.completed is True
    assert terminal.final_state == "escalate"
    assert [event.sequence for event in events] == [1]
    assert events[0].payload["reason_codes"] == ["worker_attempts_exhausted"]
