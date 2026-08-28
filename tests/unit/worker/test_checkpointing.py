from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.workflow.test_parliament_workflow import _request

from civitas.workflow.checkpointing import CheckpointConflictError, InMemoryWorkflowCheckpointStore
from civitas.workflow.models import WorkflowCheckpoint, WorkflowPhase


@pytest.mark.asyncio
async def test_only_one_worker_can_claim_a_transition() -> None:
    store = InMemoryWorkflowCheckpointStore()
    now = datetime(2026, 8, 27, tzinfo=UTC)
    checkpoint = WorkflowCheckpoint(
        planning_run_id="run-1",
        phase=WorkflowPhase.PROPOSAL,
        cycle=1,
        optimization_request=_request(),
    )
    await store.enqueue(checkpoint)

    first = await store.claim(worker_id="worker-a", now=now, lease_for=timedelta(minutes=1))
    second = await store.claim(worker_id="worker-b", now=now, lease_for=timedelta(minutes=1))

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_expired_lease_is_recoverable_and_old_worker_cannot_commit() -> None:
    store = InMemoryWorkflowCheckpointStore()
    now = datetime(2026, 8, 27, tzinfo=UTC)
    checkpoint = WorkflowCheckpoint(
        planning_run_id="run-1",
        phase=WorkflowPhase.PROPOSAL,
        cycle=1,
        optimization_request=_request(),
    )
    await store.enqueue(checkpoint)
    old = await store.claim(worker_id="worker-a", now=now, lease_for=timedelta(seconds=1))
    assert old is not None
    assert await store.recover_abandoned(now=now + timedelta(seconds=2)) == 1
    replacement = await store.claim(
        worker_id="worker-b", now=now + timedelta(seconds=2), lease_for=timedelta(minutes=1)
    )
    assert replacement is not None
    with pytest.raises(CheckpointConflictError):
        await store.commit_transition(
            lease=old, checkpoint=checkpoint, events=(), now=now + timedelta(seconds=2)
        )


@pytest.mark.asyncio
async def test_lease_renewal_extends_ownership_without_changing_its_token() -> None:
    store = InMemoryWorkflowCheckpointStore()
    now = datetime(2026, 8, 27, tzinfo=UTC)
    checkpoint = WorkflowCheckpoint(
        planning_run_id="run-1",
        phase=WorkflowPhase.PROPOSAL,
        cycle=1,
        optimization_request=_request(),
    )
    await store.enqueue(checkpoint)
    lease = await store.claim(worker_id="worker-a", now=now, lease_for=timedelta(seconds=1))
    assert lease is not None

    renewed = await store.renew(
        lease=lease,
        now=now + timedelta(milliseconds=500),
        lease_for=timedelta(seconds=2),
    )

    assert renewed.token == lease.token
    assert renewed.expires_at == now + timedelta(milliseconds=2500)
    assert (
        await store.claim(
            worker_id="worker-b",
            now=now + timedelta(milliseconds=1500),
            lease_for=timedelta(seconds=1),
        )
        is None
    )
    await store.commit_transition(
        lease=lease,
        checkpoint=checkpoint,
        events=(),
        now=now + timedelta(milliseconds=1500),
    )
