from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from civitas.api.guarded_api import WorkflowAPIService, WorkflowStore, create_guarded_app
from civitas.contracts import (
    CandidatePlan,
    FeasibilityStatus,
    IntegrityComponents,
    JuryEvaluation,
    JuryGateResult,
    JuryRequest,
    OptimizationRequest,
    OptimizationResult,
)
from civitas.persistence.database import Database
from civitas.persistence.models import OrganizationModel, PlanningRunModel
from civitas.workflow import ParliamentWorkflow


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeIDs:
    def __init__(self) -> None:
        self._value = 0
        self._prefix = str(uuid4())

    def new_id(self, namespace: str) -> str:
        self._value += 1
        return f"{namespace}-{self._prefix}-{self._value}"


class FakeOptimizer:
    async def solve(self, request: OptimizationRequest) -> OptimizationResult:
        return OptimizationResult(
            planning_run_id=request.planning_run_id,
            alternatives=(
                CandidatePlan(
                    plan_id="plan-a",
                    planning_run_id=request.planning_run_id,
                    feasibility=FeasibilityStatus.FULLY_FEASIBLE,
                    shortage_base_units=0,
                    metrics={"fulfillment": Decimal("10"), "total_landed_cost": Decimal("5")},
                    solver_version="solver-1",
                ),
            ),
        )


class FakeJury:
    async def evaluate(self, request: JuryRequest) -> JuryEvaluation:
        return JuryEvaluation(
            evaluation_id="jury-1",
            planning_run_id=request.planning_run_id,
            plan_id=request.candidate_plan.plan_id,
            policy_version="decision-integrity-v1",
            implementation_version="impl-1",
            calculated_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
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
            gates=(JuryGateResult(gate_code="ok", passed=True),),
            state="approve",
            reason_codes=(),
        )


class StubExecutionService:
    async def execute(self, _request: object) -> object:
        raise AssertionError("execution endpoint is not used in this test")


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


async def seed_planning_run(database: Database, planning_run_id: str) -> str:
    organization_id = identifier("org")
    async with database.unit_of_work() as uow:
        session = uow.require_session()
        session.add(OrganizationModel(id=organization_id, name="API Org", timezone="UTC"))
        session.add(
            PlanningRunModel(
                id=planning_run_id,
                organization_id=organization_id,
                horizon_start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
                horizon_end=datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
                bucket_duration=timedelta(days=1),
                timezone="UTC",
                input_data_version="inputs-v1",
                status="created",
            )
        )
    return organization_id


@pytest.mark.asyncio(loop_scope="session")
async def test_workflow_api_supports_start_inspect_resume_and_sse_cursor(
    database: Database,
) -> None:
    planning_run_id = identifier("run")
    organization_id = await seed_planning_run(database, planning_run_id)

    clock = FakeClock(datetime(2026, 8, 27, 12, 0, tzinfo=UTC))
    ids = FakeIDs()
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer(),
        jury=FakeJury(),
        ids=ids,
        clock=clock,
    )
    store = WorkflowStore(sessions=database.sessions, ids=ids, clock=clock)
    service = WorkflowAPIService(workflow=workflow, store=store)
    api_token = "integration-test-token-with-32-characters"
    app = create_guarded_app(
        workflow_service=service,
        execution_service=StubExecutionService(),
        api_token=api_token,
        organization_id=organization_id,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {api_token}"},
    ) as client:
        start = await client.post(
            f"/planning-runs/{planning_run_id}/start",
            json={
                "optimization_request": {
                    "planning_run_id": "ignored",
                    "input_data_version": "inputs-v1",
                    "objectives_version": "objectives-v1",
                    "constraints": {
                        "plan_annotations": {
                            "plan-a": {"claim_ids": ["claim-1"], "evidence_ids": ["e-1"]}
                        }
                    },
                    "maximum_alternatives": 1,
                },
                "limits": {
                    "max_cycles": 3,
                    "max_tool_calls": 3,
                    "max_cost": "0",
                    "max_repeated_evidence": 1,
                    "deadline_at": "2026-08-27T13:00:00Z",
                },
                "mode": "initialize_only",
            },
        )
        assert start.status_code == 200
        assert start.json()["event_sequence"] == 1
        assert start.json()["phase"] == "proposal"

        resume = await client.post(
            f"/planning-runs/{planning_run_id}/resume",
            json={
                "limits": {
                    "max_cycles": 3,
                    "max_tool_calls": 3,
                    "max_cost": "0",
                    "max_repeated_evidence": 1,
                    "deadline_at": "2026-08-27T13:00:00Z",
                },
                "mode": "to_completion",
            },
        )
        assert resume.status_code == 200
        assert resume.json()["final_state"] == "approve"

        inspect = await client.get(f"/planning-runs/{planning_run_id}")
        assert inspect.status_code == 200
        payload = inspect.json()
        assert payload["checkpoint"]["completed"] is True
        assert payload["events"][0]["event_type"] == "run.started"

        stream = await client.get(
            f"/planning-runs/{planning_run_id}/stream",
            headers={"Last-Event-ID": "1"},
        )
        assert stream.status_code == 200
        body = stream.text
        assert "id: 1" not in body
        assert "event: proposal.created" in body


@pytest.mark.asyncio(loop_scope="session")
async def test_workflow_api_rejects_missing_authentication(database: Database) -> None:
    planning_run_id = identifier("run")
    organization_id = await seed_planning_run(database, planning_run_id)
    clock = FakeClock(datetime(2026, 8, 27, 12, 0, tzinfo=UTC))
    ids = FakeIDs()
    service = WorkflowAPIService(
        workflow=ParliamentWorkflow(
            optimizer=FakeOptimizer(), jury=FakeJury(), ids=ids, clock=clock
        ),
        store=WorkflowStore(sessions=database.sessions, ids=ids, clock=clock),
    )
    app = create_guarded_app(
        workflow_service=service,
        execution_service=StubExecutionService(),
        api_token="integration-test-token-with-32-characters",
        organization_id=organization_id,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/planning-runs/{planning_run_id}")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio(loop_scope="session")
async def test_workflow_api_rejects_cross_organization_run_access(database: Database) -> None:
    owned_run_id = identifier("run")
    organization_id = await seed_planning_run(database, owned_run_id)
    foreign_run_id = identifier("run")
    await seed_planning_run(database, foreign_run_id)
    clock = FakeClock(datetime(2026, 8, 27, 12, 0, tzinfo=UTC))
    ids = FakeIDs()
    service = WorkflowAPIService(
        workflow=ParliamentWorkflow(
            optimizer=FakeOptimizer(), jury=FakeJury(), ids=ids, clock=clock
        ),
        store=WorkflowStore(sessions=database.sessions, ids=ids, clock=clock),
    )
    token = "integration-test-token-with-32-characters"
    app = create_guarded_app(
        workflow_service=service,
        execution_service=StubExecutionService(),
        api_token=token,
        organization_id=organization_id,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        response = await client.get(f"/planning-runs/{foreign_run_id}")

    assert response.status_code == 403
