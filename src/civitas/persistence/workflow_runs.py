"""Organization-scoped durable workflow adapter for the product facade."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civitas.application.planning_inputs import ProviderPlanningInputAssembler
from civitas.application.procurement_facade import WorkflowRunSnapshot
from civitas.contracts.enums import WorkflowEventType
from civitas.contracts.mcp_product import PlanningProgress, PlanningRunStatus, ProcurementGoal
from civitas.contracts.optimization import OptimizationRequest
from civitas.persistence.evidence import PostgreSQLEvidenceLedger
from civitas.persistence.models import (
    OrganizationModel,
    PlanningBucketModel,
    PlanningRunModel,
    SKUModel,
    WarehouseModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
)
from civitas.ports.clock import Clock
from civitas.ports.identity import OperatorContext
from civitas.ports.ids import IDGenerator
from civitas.workflow.models import WorkflowCheckpoint, WorkflowLimits
from civitas.workflow.orchestrator import ParliamentWorkflow


class PostgreSQLWorkflowRunStore:
    """Join product-level run creation to the durable checkpoint queue."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        workflow: ParliamentWorkflow,
        ids: IDGenerator,
        clock: Clock,
        policy_version: str,
        input_assembler: ProviderPlanningInputAssembler | None = None,
        evidence_ledger: PostgreSQLEvidenceLedger | None = None,
    ) -> None:
        if not policy_version.strip():
            raise ValueError("policy_version is required")
        self._sessions = sessions
        self._workflow = workflow
        self._ids = ids
        self._clock = clock
        self._policy_version = policy_version
        self._input_assembler = input_assembler
        self._evidence_ledger = evidence_ledger
        if (input_assembler is None) != (evidence_ledger is None):
            raise ValueError("planning input assembly requires its durable evidence ledger")

    async def start(
        self,
        *,
        context: OperatorContext,
        run_id: str,
        goal: ProcurementGoal,
        optimization_request: OptimizationRequest,
        limits: WorkflowLimits,
    ) -> WorkflowRunSnapshot:
        if optimization_request.planning_run_id != run_id:
            raise ValueError("optimization request belongs to a different run")
        prepared = (
            None
            if self._input_assembler is None
            else await self._input_assembler.prepare(
                organization_id=context.organization_id,
                run_id=run_id,
                goal=goal,
                base_request=optimization_request,
            )
        )
        if prepared is not None:
            optimization_request = prepared.optimization_request
        now = self._clock.now()
        checkpoint = self._workflow.start(
            planning_run_id=run_id, optimization_request=optimization_request
        )
        try:
            async with self._sessions() as session, session.begin():
                organization = await session.get(OrganizationModel, context.organization_id)
                if organization is None:
                    raise ValueError("organization is not provisioned")
                if organization.timezone != goal.timezone:
                    raise ValueError("goal timezone does not match the organization timezone")
                await _require_scoped_inputs(session, context.organization_id, goal)
                planning_run = PlanningRunModel(
                    id=run_id,
                    organization_id=context.organization_id,
                    horizon_start=goal.horizon_starts_at,
                    horizon_end=goal.horizon_ends_at,
                    bucket_duration=timedelta(days=1),
                    timezone=goal.timezone,
                    input_data_version=optimization_request.input_data_version,
                    status=PlanningRunStatus.PLANNING.value,
                )
                session.add(planning_run)
                # The mappings intentionally have no ORM relationships. Flush the
                # parent explicitly before adding FK-dependent queue and bucket rows.
                await session.flush()
                if prepared is not None:
                    assert self._evidence_ledger is not None
                    for read, claims in prepared.reads_and_claims:
                        await self._evidence_ledger.persist_read_in_session(
                            session,
                            planning_run_id=run_id,
                            read=read,
                            claims=claims,
                        )
                for sequence, (starts_at, ends_at) in enumerate(_planning_buckets(goal)):
                    session.add(
                        PlanningBucketModel(
                            id=self._ids.new_id("bucket"),
                            planning_run_id=run_id,
                            sequence=sequence,
                            starts_at=starts_at,
                            ends_at=ends_at,
                        )
                    )
                session.add(
                    WorkflowCheckpointModel(
                        planning_run_id=run_id,
                        checkpoint=checkpoint.model_dump(mode="json"),
                        workflow_limits=limits.model_dump(mode="json"),
                        procurement_goal=goal.model_dump(mode="json"),
                        policy_version=self._policy_version,
                        phase=checkpoint.phase.value,
                        cycle=checkpoint.cycle,
                        event_sequence=checkpoint.event_sequence,
                        completed=checkpoint.completed,
                        available_at=now,
                        updated_at=now,
                    )
                )
        except IntegrityError as error:
            raise ValueError(
                "planning run already exists or contains invalid references"
            ) from error
        return WorkflowRunSnapshot(
            organization_id=context.organization_id,
            run_id=run_id,
            policy_version=self._policy_version,
            created_at=now,
            updated_at=now,
            checkpoint=checkpoint,
        )

    async def get(self, *, context: OperatorContext, run_id: str) -> WorkflowRunSnapshot | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(PlanningRunModel, WorkflowCheckpointModel)
                    .join(
                        WorkflowCheckpointModel,
                        WorkflowCheckpointModel.planning_run_id == PlanningRunModel.id,
                    )
                    .where(
                        PlanningRunModel.id == run_id,
                        PlanningRunModel.organization_id == context.organization_id,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            planning_run, durable = row._tuple()
            event_rows = (
                await session.scalars(
                    select(WorkflowEventModel)
                    .where(
                        WorkflowEventModel.planning_run_id == run_id,
                        WorkflowEventModel.sequence <= durable.event_sequence,
                    )
                    .order_by(WorkflowEventModel.sequence)
                )
            ).all()
        checkpoint = WorkflowCheckpoint.model_validate(durable.checkpoint)
        return WorkflowRunSnapshot(
            organization_id=planning_run.organization_id,
            run_id=planning_run.id,
            policy_version=durable.policy_version or self._policy_version,
            created_at=planning_run.created_at,
            updated_at=durable.updated_at,
            checkpoint=checkpoint,
            events=tuple(_progress(row) for row in event_rows),
        )


async def _require_scoped_inputs(
    session: AsyncSession, organization_id: str, goal: ProcurementGoal
) -> None:
    sku_ids = set(
        await session.scalars(
            select(SKUModel.id).where(
                SKUModel.organization_id == organization_id,
                SKUModel.id.in_(goal.sku_ids),
            )
        )
    )
    if sku_ids != set(goal.sku_ids):
        raise ValueError("one or more SKUs are not available to the organization")
    warehouse_ids = set(
        await session.scalars(
            select(WarehouseModel.id).where(
                WarehouseModel.organization_id == organization_id,
                WarehouseModel.id.in_(goal.warehouse_ids),
            )
        )
    )
    if warehouse_ids != set(goal.warehouse_ids):
        raise ValueError("one or more warehouses are not available to the organization")


def _planning_buckets(goal: ProcurementGoal) -> tuple[tuple[datetime, datetime], ...]:
    try:
        timezone = ZoneInfo(goal.timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError("goal timezone is not recognized") from error
    cursor = goal.horizon_starts_at
    buckets: list[tuple[datetime, datetime]] = []
    while cursor < goal.horizon_ends_at:
        local = cursor.astimezone(timezone)
        next_date = local.date() + timedelta(days=1)
        next_boundary = datetime.combine(next_date, time.min, timezone).astimezone(UTC)
        if next_boundary <= cursor:
            raise ValueError("could not construct monotonic planning buckets")
        ends_at = min(next_boundary, goal.horizon_ends_at)
        buckets.append((cursor, ends_at))
        cursor = ends_at
    return tuple(buckets)


def _progress(row: WorkflowEventModel) -> PlanningProgress:
    nested = row.payload.get("event")
    payload = nested if isinstance(nested, dict) else row.payload
    phase = payload.get("phase")
    raw_reason_codes = payload.get("reason_codes", [])
    reason_codes = (
        tuple(code for code in raw_reason_codes if isinstance(code, str))
        if isinstance(raw_reason_codes, list)
        else ()
    )
    return PlanningProgress(
        sequence=row.sequence,
        occurred_at=row.occurred_at,
        phase=phase if isinstance(phase, str) else WorkflowEventType(row.event_type).value,
        message=row.event_type.replace(".", " ").capitalize(),
        reason_codes=reason_codes,
    )
