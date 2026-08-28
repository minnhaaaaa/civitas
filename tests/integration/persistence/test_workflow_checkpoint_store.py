import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from tests.unit.workflow.test_parliament_workflow import _request

from civitas.contracts.enums import FeasibilityStatus, JuryState, WorkflowEventType
from civitas.contracts.jury import IntegrityComponents, JuryEvaluation, JuryGateResult
from civitas.contracts.optimization import CandidatePlan, OptimizationResult
from civitas.contracts.workflow import WorkflowEvent
from civitas.persistence.models import (
    CandidatePlanModel,
    DistributionLineModel,
    JuryDecisionModel,
    OrganizationModel,
    PlanningRunModel,
    ProcurementLineModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
)
from civitas.persistence.workflow import PostgreSQLWorkflowCheckpointStore
from civitas.workflow.checkpointing import CheckpointConflictError
from civitas.workflow.models import ParliamentSession, WorkflowCheckpoint, WorkflowPhase


async def _seed_run(database: object) -> tuple[str, str]:
    run_id = f"run-{uuid4().hex}"
    organization_id = f"org-{uuid4().hex}"
    now = datetime.now(UTC)
    sessions = database.sessions  # type: ignore[attr-defined]
    async with sessions() as session, session.begin():
        session.add(OrganizationModel(id=organization_id, name="Test", timezone="UTC"))
        session.add(
            PlanningRunModel(
                id=run_id,
                organization_id=organization_id,
                horizon_start=now,
                horizon_end=now + timedelta(days=7),
                bucket_duration=timedelta(days=1),
                timezone="UTC",
                input_data_version="inputs-v1",
                status="created",
            )
        )
    return run_id, organization_id


@pytest_asyncio.fixture
async def planning_run(database: object) -> AsyncIterator[str]:
    run_id, organization_id = await _seed_run(database)
    try:
        yield run_id
    finally:
        sessions = database.sessions  # type: ignore[attr-defined]
        async with sessions() as session, session.begin():
            plan_ids = select(CandidatePlanModel.id).where(
                CandidatePlanModel.planning_run_id == run_id
            )
            await session.execute(
                delete(JuryDecisionModel).where(JuryDecisionModel.planning_run_id == run_id)
            )
            await session.execute(
                delete(ProcurementLineModel).where(ProcurementLineModel.plan_id.in_(plan_ids))
            )
            await session.execute(
                delete(DistributionLineModel).where(DistributionLineModel.plan_id.in_(plan_ids))
            )
            await session.execute(
                delete(CandidatePlanModel).where(CandidatePlanModel.planning_run_id == run_id)
            )
            await session.execute(
                delete(WorkflowEventModel).where(WorkflowEventModel.planning_run_id == run_id)
            )
            await session.execute(
                delete(WorkflowCheckpointModel).where(
                    WorkflowCheckpointModel.planning_run_id == run_id
                )
            )
            await session.execute(delete(PlanningRunModel).where(PlanningRunModel.id == run_id))
            await session.execute(
                delete(OrganizationModel).where(OrganizationModel.id == organization_id)
            )


def _checkpoint(run_id: str) -> WorkflowCheckpoint:
    return WorkflowCheckpoint(
        planning_run_id=run_id,
        phase=WorkflowPhase.PROPOSAL,
        cycle=1,
        optimization_request=_request(planning_run_id=run_id),
    )


@pytest.mark.asyncio
async def test_postgresql_claim_is_exclusive_and_checkpoint_survives_restart(
    database: object,
    planning_run: str,
) -> None:
    run_id = planning_run
    first_in_queue = datetime(2000, 1, 1, tzinfo=UTC)
    first_store = PostgreSQLWorkflowCheckpointStore(  # type: ignore[attr-defined]
        database.sessions, now_factory=lambda: first_in_queue
    )
    second_store = PostgreSQLWorkflowCheckpointStore(  # type: ignore[attr-defined]
        database.sessions, now_factory=lambda: first_in_queue
    )
    await first_store.enqueue(_checkpoint(run_id))
    now = datetime.now(UTC) + timedelta(seconds=1)

    first, second = await asyncio.gather(
        first_store.claim(worker_id="worker-a", now=now, lease_for=timedelta(minutes=1)),
        second_store.claim(worker_id="worker-b", now=now, lease_for=timedelta(minutes=1)),
    )

    leases = [
        lease for lease in (first, second) if lease is not None and lease.planning_run_id == run_id
    ]
    assert len(leases) == 1
    lease = leases[0]
    event = WorkflowEvent(
        event_id=f"event-{uuid4().hex}",
        planning_run_id=run_id,
        sequence=1,
        event_type=WorkflowEventType.RUN_STARTED,
        occurred_at=now,
        payload={"phase": "proposal", "cycle": 1},
    )
    updated = lease.checkpoint.model_copy(update={"event_sequence": 1})
    await first_store.commit_transition(
        lease=lease,
        checkpoint=updated,
        events=(event,),
        now=now + timedelta(seconds=1),
    )

    restarted_store = PostgreSQLWorkflowCheckpointStore(database.sessions)  # type: ignore[attr-defined]
    assert await restarted_store.get_checkpoint(run_id) == updated
    assert await restarted_store.list_events(
        planning_run_id=run_id, after_sequence=0, limit=10
    ) == (event,)


@pytest.mark.asyncio
async def test_postgresql_recovers_abandoned_work_and_rejects_stale_owner(
    database: object, planning_run: str
) -> None:
    run_id = planning_run
    store = PostgreSQLWorkflowCheckpointStore(  # type: ignore[attr-defined]
        database.sessions, now_factory=lambda: datetime(2000, 1, 2, tzinfo=UTC)
    )
    checkpoint = _checkpoint(run_id)
    await store.enqueue(checkpoint)
    now = datetime.now(UTC) + timedelta(seconds=1)
    stale = await store.claim(worker_id="worker-a", now=now, lease_for=timedelta(milliseconds=100))
    assert stale is not None

    recovered_at = now + timedelta(seconds=1)
    assert await store.recover_abandoned(now=recovered_at) >= 1
    sessions = database.sessions  # type: ignore[attr-defined]
    async with sessions() as session:
        recovered = await session.scalar(
            select(WorkflowCheckpointModel).where(WorkflowCheckpointModel.planning_run_id == run_id)
        )
    assert recovered is not None and recovered.lease_token is None
    with pytest.raises(CheckpointConflictError):
        await store.commit_transition(
            lease=stale,
            checkpoint=checkpoint,
            events=(),
            now=recovered_at,
        )


@pytest.mark.asyncio
async def test_postgresql_lease_renewal_prevents_early_reclaim(
    database: object, planning_run: str
) -> None:
    store = PostgreSQLWorkflowCheckpointStore(  # type: ignore[attr-defined]
        database.sessions, now_factory=lambda: datetime(2000, 1, 2, tzinfo=UTC)
    )
    checkpoint = _checkpoint(planning_run)
    await store.enqueue(checkpoint)
    now = datetime.now(UTC) + timedelta(seconds=1)
    lease = await store.claim(worker_id="worker-a", now=now, lease_for=timedelta(seconds=1))
    assert lease is not None

    renewed = await store.renew(
        lease=lease,
        now=now + timedelta(milliseconds=500),
        lease_for=timedelta(seconds=2),
    )

    assert renewed.token == lease.token
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


@pytest.mark.asyncio
async def test_postgresql_rejects_non_monotonic_progress_events(
    database: object, planning_run: str
) -> None:
    run_id = planning_run
    store = PostgreSQLWorkflowCheckpointStore(  # type: ignore[attr-defined]
        database.sessions, now_factory=lambda: datetime(2000, 1, 3, tzinfo=UTC)
    )
    checkpoint = _checkpoint(run_id)
    await store.enqueue(checkpoint)
    now = datetime.now(UTC) + timedelta(seconds=1)
    lease = await store.claim(worker_id="worker-a", now=now, lease_for=timedelta(minutes=1))
    assert lease is not None
    skipped = WorkflowEvent(
        event_id=f"event-{uuid4().hex}",
        planning_run_id=run_id,
        sequence=2,
        event_type=WorkflowEventType.TASK_STARTED,
        occurred_at=now,
    )

    with pytest.raises(CheckpointConflictError, match="contiguous"):
        await store.commit_transition(
            lease=lease,
            checkpoint=checkpoint.model_copy(update={"event_sequence": 2}),
            events=(skipped,),
            now=now + timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_approved_checkpoint_projects_selected_plan_and_jury_for_execution(
    database: object, planning_run: str
) -> None:
    store = PostgreSQLWorkflowCheckpointStore(database.sessions)  # type: ignore[attr-defined]
    initial = _checkpoint(planning_run)
    await store.enqueue(initial)
    now = datetime.now(UTC) + timedelta(seconds=1)
    lease = await store.claim(worker_id="worker-a", now=now, lease_for=timedelta(minutes=1))
    assert lease is not None
    plan_id = f"plan-{uuid4().hex}"
    plan = CandidatePlan(
        plan_id=plan_id,
        planning_run_id=planning_run,
        feasibility=FeasibilityStatus.FULLY_FEASIBLE,
        shortage_base_units=0,
        metrics={"total_landed_cost": Decimal("0")},
        solver_version="solver-test-v1",
    )
    jury_id = f"jury-{uuid4().hex}"
    approved = initial.model_copy(
        update={
            "phase": WorkflowPhase.APPROVE,
            "optimization_result": OptimizationResult(
                planning_run_id=planning_run,
                alternatives=(plan,),
            ),
            "parliament": ParliamentSession(selected_plan_id=plan_id),
            "jury_evaluation": JuryEvaluation(
                evaluation_id=jury_id,
                planning_run_id=planning_run,
                plan_id=plan_id,
                policy_version="decision-integrity-v1",
                implementation_version="test-v1",
                calculated_at=now,
                components=IntegrityComponents(
                    critical_claim_coverage=100,
                    evidence_independence=100,
                    provenance_completeness=100,
                    evidence_freshness=100,
                    canonical_source_diversity=100,
                    contradiction_resolution=100,
                    dissent_robustness=100,
                ),
                integrity_score=100,
                gates=(JuryGateResult(gate_code="solver_feasibility", passed=True),),
                state=JuryState.APPROVE,
                reason_codes=(),
            ),
            "final_state": JuryState.APPROVE.value,
            "completed": True,
        }
    )

    await store.commit_transition(
        lease=lease,
        checkpoint=approved,
        events=(),
        now=now + timedelta(seconds=1),
    )

    sessions = database.sessions  # type: ignore[attr-defined]
    async with sessions() as session:
        plan_row = await session.get(CandidatePlanModel, plan_id)
        jury_row = await session.get(JuryDecisionModel, jury_id)
        run_row = await session.get(PlanningRunModel, planning_run)
    assert plan_row is not None and plan_row.selected is True
    assert jury_row is not None and jury_row.plan_id == plan_id
    assert run_row is not None and run_row.status == "ready_for_approval"
