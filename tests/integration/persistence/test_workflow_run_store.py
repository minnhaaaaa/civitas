from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from tests.integration.persistence.conftest import database_url
from tests.unit.workflow.test_parliament_workflow import (
    FakeClock,
    FakeJury,
    FakeOptimizer,
    _evaluation,
    _request,
    _result,
)

from civitas.contracts.mcp_product import (
    PlanningRunStatus,
    PlanProcurementGoalRequest,
    ProcurementGoal,
)
from civitas.persistence.models import (
    OrganizationModel,
    PlanningBucketModel,
    PlanningRunModel,
    SKUModel,
    WarehouseModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
)
from civitas.persistence.workflow import PostgreSQLWorkflowCheckpointStore
from civitas.persistence.workflow_runs import PostgreSQLWorkflowRunStore
from civitas.ports.identity import OperatorContext
from civitas.runtime import RuntimeSettings, build_runtime
from civitas.runtime.adapters import UUIDGenerator
from civitas.worker import DurableWorkflowWorker
from civitas.workflow import ParliamentWorkflow, WorkflowLimits
from civitas.workflow.models import WorkflowPhase


@pytest_asyncio.fixture
async def provisioned_scope(database: object) -> AsyncIterator[tuple[str, str, str]]:
    suffix = uuid4().hex
    organization_id = f"org-{suffix}"
    sku_id = f"sku-{suffix}"
    warehouse_id = f"warehouse-{suffix}"
    sessions = database.sessions  # type: ignore[attr-defined]
    async with sessions() as session, session.begin():
        session.add(OrganizationModel(id=organization_id, name="Test", timezone="UTC"))
        session.add(
            SKUModel(
                id=sku_id,
                organization_id=organization_id,
                code=f"SKU-{suffix}",
                name="Test SKU",
                unit_of_measure="unit",
                base_unit_scale=1,
                attributes={},
            )
        )
        session.add(
            WarehouseModel(
                id=warehouse_id,
                organization_id=organization_id,
                code=f"WH-{suffix}",
                name="Test warehouse",
                timezone="UTC",
                attributes={},
            )
        )
    try:
        yield organization_id, sku_id, warehouse_id
    finally:
        async with sessions() as session, session.begin():
            run_ids = tuple(
                await session.scalars(
                    select(PlanningRunModel.id).where(
                        PlanningRunModel.organization_id == organization_id
                    )
                )
            )
            if run_ids:
                await session.execute(
                    delete(WorkflowEventModel).where(
                        WorkflowEventModel.planning_run_id.in_(run_ids)
                    )
                )
                await session.execute(
                    delete(WorkflowCheckpointModel).where(
                        WorkflowCheckpointModel.planning_run_id.in_(run_ids)
                    )
                )
                await session.execute(
                    delete(PlanningBucketModel).where(
                        PlanningBucketModel.planning_run_id.in_(run_ids)
                    )
                )
                await session.execute(
                    delete(PlanningRunModel).where(PlanningRunModel.id.in_(run_ids))
                )
            await session.execute(delete(SKUModel).where(SKUModel.id == sku_id))
            await session.execute(delete(WarehouseModel).where(WarehouseModel.id == warehouse_id))
            await session.execute(
                delete(OrganizationModel).where(OrganizationModel.id == organization_id)
            )


def _context(organization_id: str) -> OperatorContext:
    return OperatorContext(
        organization_id=organization_id,
        operator_id="operator-1",
        authentication_subject="subject-1",
        authenticated_at=datetime(2026, 8, 28, tzinfo=UTC),
        roles=("procurement-operator",),
    )


def _goal(sku_id: str, warehouse_id: str) -> ProcurementGoal:
    starts_at = datetime(2026, 8, 28, tzinfo=UTC)
    return ProcurementGoal(
        objective="Protect demand with minimal waste",
        horizon_starts_at=starts_at,
        horizon_ends_at=starts_at + timedelta(days=2, hours=12),
        timezone="UTC",
        sku_ids=(sku_id,),
        warehouse_ids=(warehouse_id,),
        maximum_cycles=2,
        model_call_budget=10,
        tool_call_budget=20,
        deadline_at=starts_at + timedelta(days=3),
    )


@pytest.mark.asyncio
async def test_facade_store_persists_tenant_run_limits_buckets_and_progress(
    database: object, provisioned_scope: tuple[str, str, str]
) -> None:
    organization_id, sku_id, warehouse_id = provisioned_scope
    run_id = f"run-{uuid4().hex}"
    now = datetime(2026, 8, 28, tzinfo=UTC)
    clock = FakeClock(now)
    ids = UUIDGenerator()
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result(run_id)]),
        jury=FakeJury([_evaluation("approve").model_copy(update={"planning_run_id": run_id})]),
        ids=ids,
        clock=clock,
    )
    checkpoints = PostgreSQLWorkflowCheckpointStore(
        database.sessions,
        now_factory=lambda: now,  # type: ignore[attr-defined]
    )
    runs = PostgreSQLWorkflowRunStore(
        sessions=database.sessions,  # type: ignore[attr-defined]
        workflow=workflow,
        ids=ids,
        clock=clock,
        policy_version="decision-integrity-v1",
    )
    limits = WorkflowLimits(max_cycles=2, max_tool_calls=20, deadline_at=now + timedelta(days=3))

    snapshot = await runs.start(
        context=_context(organization_id),
        run_id=run_id,
        goal=_goal(sku_id, warehouse_id),
        optimization_request=_request(planning_run_id=run_id),
        limits=limits,
    )

    assert snapshot.organization_id == organization_id
    lease = await checkpoints.claim(worker_id="worker-1", now=now, lease_for=timedelta(minutes=1))
    assert lease is not None and lease.planning_run_id == run_id
    assert lease.limits == limits
    await checkpoints.release(lease)
    sessions = database.sessions  # type: ignore[attr-defined]
    async with sessions() as session:
        buckets = (
            await session.scalars(
                select(PlanningBucketModel)
                .where(PlanningBucketModel.planning_run_id == run_id)
                .order_by(PlanningBucketModel.sequence)
            )
        ).all()
    assert len(buckets) == 3

    worker = DurableWorkflowWorker(
        worker_id="worker-2", workflow=workflow, store=checkpoints, clock=clock
    )
    assert await worker.process_next() is True
    resumed = await runs.get(context=_context(organization_id), run_id=run_id)
    assert resumed is not None
    assert resumed.checkpoint.phase is WorkflowPhase.CHALLENGE
    assert [event.sequence for event in resumed.events] == [1, 2]
    assert await runs.get(context=_context("another-org"), run_id=run_id) is None


@pytest.mark.asyncio
async def test_facade_store_rejects_cross_tenant_operational_inputs(
    database: object, provisioned_scope: tuple[str, str, str]
) -> None:
    organization_id, sku_id, warehouse_id = provisioned_scope
    run_id = f"run-{uuid4().hex}"
    now = datetime(2026, 8, 28, tzinfo=UTC)
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result(run_id)]),
        jury=FakeJury([_evaluation("approve")]),
        ids=UUIDGenerator(),
        clock=FakeClock(now),
    )
    runs = PostgreSQLWorkflowRunStore(
        sessions=database.sessions,  # type: ignore[attr-defined]
        workflow=workflow,
        ids=UUIDGenerator(),
        clock=FakeClock(now),
        policy_version="decision-integrity-v1",
    )

    with pytest.raises(ValueError, match="SKUs"):
        await runs.start(
            context=_context(organization_id),
            run_id=run_id,
            goal=_goal("foreign-sku", warehouse_id),
            optimization_request=_request(planning_run_id=run_id),
            limits=WorkflowLimits(max_cycles=2, deadline_at=now + timedelta(days=3)),
        )
    sessions = database.sessions  # type: ignore[attr-defined]
    async with sessions() as session:
        assert await session.get(PlanningRunModel, run_id) is None
        assert sku_id != "foreign-sku"


@pytest.mark.asyncio
async def test_agent_one_runtime_uses_postgresql_workflow_store_by_default(
    provisioned_scope: tuple[str, str, str],
) -> None:
    organization_id, sku_id, warehouse_id = provisioned_scope
    runtime = build_runtime(
        RuntimeSettings(
            database_url=database_url(),
            approval_secret_pepper="p" * 32,
            bearer_token="t" * 32,
            organization_id=organization_id,
            operator_id="operator-1",
            operator_subject="subject-1",
            operator_roles=("procurement-operator",),
        )
    )
    try:
        response = await runtime.facade.plan_procurement_goal(
            runtime.identity.context(),
            PlanProcurementGoalRequest(goal=_goal(sku_id, warehouse_id)),
        )
        assert response.run.organization_id == organization_id
        assert response.run.status is PlanningRunStatus.PLANNING
        durable = await runtime.checkpoints.get_checkpoint(response.run.run_id)
        assert durable is not None and durable.event_sequence == 0
    finally:
        await runtime.close()
