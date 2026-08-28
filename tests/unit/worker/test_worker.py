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
from civitas.workflow.checkpointing import InMemoryWorkflowCheckpointStore
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
