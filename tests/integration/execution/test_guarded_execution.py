from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from civitas.contracts.claims import ClaimScope, TypedClaim
from civitas.contracts.enums import EvidenceOrigin, FeasibilityStatus
from civitas.contracts.evidence import EvidenceIdentity, EvidenceRecord
from civitas.contracts.execution import ExecutionRequest
from civitas.execution.guarded import GuardedExecutionService, RefreshBundle
from civitas.persistence.database import Database
from civitas.persistence.models import (
    CandidatePlanModel,
    JuryDecisionModel,
    OrganizationModel,
    PlanningRunModel,
    ProcurementLineModel,
    SKUModel,
    SupplierModel,
    WarehouseModel,
)
from tools.mock_mcp import MockProcurementMCPServer


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeIDs:
    def __init__(self) -> None:
        self._value = 0

    def new_id(self, namespace: str) -> str:
        self._value += 1
        return f"{namespace}-{self._value}"


class StaticRefresher:
    def __init__(self, bundle: RefreshBundle) -> None:
        self._bundle = bundle

    async def refresh(self, **_: object) -> RefreshBundle:
        return self._bundle


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def execution_request(*, planning_run_id: str, plan_id: str, jury_id: str, key: str) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id=identifier("exec"),
        planning_run_id=planning_run_id,
        approved_plan_id=plan_id,
        jury_evaluation_id=jury_id,
        idempotency_key=key,
        approval_policy_version="approval-v1",
        requested_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
        action={"source": "integration-test"},
    )


def claim(
    *,
    claim_id: str,
    predicate: str,
    observed_at: datetime,
    organization_id: str,
    sku_id: str,
    warehouse_id: str,
) -> TypedClaim:
    return TypedClaim(
        claim_id=claim_id,
        subject="offer",
        predicate=predicate,
        value=1,
        unit="unit",
        valid_at=observed_at,
        scope=ClaimScope(
            organization_id=organization_id,
            sku_id=sku_id,
            warehouse_id=warehouse_id,
        ),
        human_summary=f"{predicate} claim",
    )


def evidence(*, evidence_id: str, claim_id: str, observed_at: datetime) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        claim_ids=(claim_id,),
        identity=EvidenceIdentity(
            canonical_source_id="mock-source",
            canonical_source_type="supplier_offer",
            mcp_server="mock-procurement",
            tool_name="get_supplier_offers",
            normalized_arguments={"planning_run_id": "run"},
            retrieved_at=observed_at,
            observation_version="mock-v1",
            raw_response_sha256="0" * 64,
        ),
        origin=EvidenceOrigin.EXTERNAL,
        content_summary="supplier offer refresh",
        raw_payload={"offers": []},
    )


async def seed_execution_state(
    database: Database,
) -> tuple[str, str, str, str, str, str, str]:
    organization_id = identifier("org")
    sku_id = identifier("sku")
    warehouse_id = identifier("wh")
    supplier_id = identifier("supplier")
    planning_run_id = identifier("run")
    plan_id = identifier("plan")
    jury_id = identifier("jury")
    async with database.unit_of_work() as uow:
        session = uow.require_session()
        session.add(OrganizationModel(id=organization_id, name="Exec Org", timezone="UTC"))
        session.add(
            SKUModel(
                id=sku_id,
                organization_id=organization_id,
                code=identifier("sku"),
                name="Apples",
                unit_of_measure="kg",
                base_unit_scale=1000,
                attributes={},
            )
        )
        session.add(
            WarehouseModel(
                id=warehouse_id,
                organization_id=organization_id,
                code=identifier("wh"),
                name="Warehouse",
                timezone="UTC",
                attributes={},
            )
        )
        session.add(
            SupplierModel(
                id=supplier_id,
                organization_id=organization_id,
                code=identifier("sup"),
                name="Supplier",
                attributes={},
            )
        )
        session.add(
            PlanningRunModel(
                id=planning_run_id,
                organization_id=organization_id,
                horizon_start=datetime(2026, 8, 27, 0, 0, tzinfo=UTC),
                horizon_end=datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
                bucket_duration=timedelta(days=1),
                timezone="UTC",
                input_data_version="inputs-v1",
                status="approve",
            )
        )
        await session.flush()
        session.add(
            CandidatePlanModel(
                id=plan_id,
                planning_run_id=planning_run_id,
                stable_key="stable-plan",
                feasibility=FeasibilityStatus.FULLY_FEASIBLE.value,
                shortage_base_units=0,
                metrics={"total_landed_cost": "10"},
                solver_version="solver-1",
                selected=True,
            )
        )
        session.add(
            ProcurementLineModel(
                id=identifier("proc"),
                plan_id=plan_id,
                supplier_id=supplier_id,
                sku_id=sku_id,
                destination_warehouse_id=warehouse_id,
                arrival_bucket_start=datetime(2026, 8, 28, 0, 0, tzinfo=UTC),
                quantity=Decimal("2"),
                unit_of_measure="kg",
                landed_cost=Decimal("10"),
            )
        )
        session.add(
            JuryDecisionModel(
                id=jury_id,
                planning_run_id=planning_run_id,
                plan_id=plan_id,
                policy_version="decision-integrity-v1",
                implementation_version="impl-1",
                calculated_at=datetime(2026, 8, 27, 11, 55, tzinfo=UTC),
                component_scores={},
                integrity_score=Decimal("99"),
                gate_results=[],
                final_state="approve",
                reason_codes=[],
                per_claim_contributions={},
            )
        )
    return planning_run_id, plan_id, jury_id, organization_id, sku_id, warehouse_id, supplier_id


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_inputs_block_execution(database: Database) -> None:
    (
        planning_run_id,
        plan_id,
        jury_id,
        organization_id,
        sku_id,
        warehouse_id,
        supplier_id,
    ) = await seed_execution_state(database)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    offer_key = f"{supplier_id}:{sku_id}:{warehouse_id}"
    refresher = StaticRefresher(
        RefreshBundle(
            claims=(
                claim(
                    claim_id="claim-1",
                    predicate="unit_price",
                    observed_at=now - timedelta(minutes=15),
                    organization_id=organization_id,
                    sku_id=sku_id,
                    warehouse_id=warehouse_id,
                ),
            ),
            evidence=(evidence(evidence_id="e-1", claim_id="claim-1", observed_at=now - timedelta(minutes=15)),),
            observed_unit_prices={offer_key: Decimal("5")},
            constraints={f"offer:{offer_key}:available": Decimal("10")},
        )
    )
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=MockProcurementMCPServer(),
        ids=FakeIDs(),
        clock=FakeClock(now),
        refresher=refresher,
    )

    outcome = await service.execute(
        execution_request(
            planning_run_id=planning_run_id,
            plan_id=plan_id,
            jury_id=jury_id,
            key=identifier("idem"),
        )
    )

    assert outcome.decision == "investigate"
    assert outcome.result.failure_code == "stale_execution_data"


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_changes_return_to_investigation(database: Database) -> None:
    (
        planning_run_id,
        plan_id,
        jury_id,
        organization_id,
        sku_id,
        warehouse_id,
        supplier_id,
    ) = await seed_execution_state(database)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    offer_key = f"{supplier_id}:{sku_id}:{warehouse_id}"
    refresher = StaticRefresher(
        RefreshBundle(
            claims=(
                claim(
                    claim_id="claim-2",
                    predicate="unit_price",
                    observed_at=now,
                    organization_id=organization_id,
                    sku_id=sku_id,
                    warehouse_id=warehouse_id,
                ),
            ),
            evidence=(evidence(evidence_id="e-2", claim_id="claim-2", observed_at=now),),
            observed_unit_prices={offer_key: Decimal("5")},
            constraints={f"offer:{offer_key}:available": Decimal("1")},
        )
    )
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=MockProcurementMCPServer(),
        ids=FakeIDs(),
        clock=FakeClock(now),
        refresher=refresher,
    )

    outcome = await service.execute(
        execution_request(
            planning_run_id=planning_run_id,
            plan_id=plan_id,
            jury_id=jury_id,
            key=identifier("idem"),
        )
    )

    assert outcome.decision == "investigate"
    assert outcome.result.failure_code == "plan_changed"


@pytest.mark.asyncio(loop_scope="session")
async def test_approved_total_cannot_be_exceeded(database: Database) -> None:
    (
        planning_run_id,
        plan_id,
        jury_id,
        organization_id,
        sku_id,
        warehouse_id,
        supplier_id,
    ) = await seed_execution_state(database)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    offer_key = f"{supplier_id}:{sku_id}:{warehouse_id}"
    refresher = StaticRefresher(
        RefreshBundle(
            claims=(
                claim(
                    claim_id="claim-3",
                    predicate="unit_price",
                    observed_at=now,
                    organization_id=organization_id,
                    sku_id=sku_id,
                    warehouse_id=warehouse_id,
                ),
            ),
            evidence=(evidence(evidence_id="e-3", claim_id="claim-3", observed_at=now),),
            observed_unit_prices={offer_key: Decimal("6")},
            constraints={f"offer:{offer_key}:available": Decimal("10")},
        )
    )
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=MockProcurementMCPServer(),
        ids=FakeIDs(),
        clock=FakeClock(now),
        refresher=refresher,
    )

    outcome = await service.execute(
        execution_request(
            planning_run_id=planning_run_id,
            plan_id=plan_id,
            jury_id=jury_id,
            key=identifier("idem"),
        )
    )

    assert outcome.decision == "escalate"
    assert outcome.result.failure_code == "approved_total_exceeded"


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_requests_do_not_duplicate_orders(database: Database) -> None:
    (
        planning_run_id,
        plan_id,
        jury_id,
        organization_id,
        sku_id,
        warehouse_id,
        supplier_id,
    ) = await seed_execution_state(database)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    offer_key = f"{supplier_id}:{sku_id}:{warehouse_id}"
    refresher = StaticRefresher(
        RefreshBundle(
            claims=(
                claim(
                    claim_id="claim-4",
                    predicate="unit_price",
                    observed_at=now,
                    organization_id=organization_id,
                    sku_id=sku_id,
                    warehouse_id=warehouse_id,
                ),
            ),
            evidence=(evidence(evidence_id="e-4", claim_id="claim-4", observed_at=now),),
            observed_unit_prices={offer_key: Decimal("5")},
            constraints={f"offer:{offer_key}:available": Decimal("10")},
        )
    )
    server = MockProcurementMCPServer()
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=server,
        ids=FakeIDs(),
        clock=FakeClock(now),
        refresher=refresher,
    )
    key = identifier("idem")

    first = await service.execute(
        execution_request(planning_run_id=planning_run_id, plan_id=plan_id, jury_id=jury_id, key=key)
    )
    second = await service.execute(
        execution_request(planning_run_id=planning_run_id, plan_id=plan_id, jury_id=jury_id, key=key)
    )

    assert first.decision == "execute"
    assert first.result.state == "succeeded"
    assert second.decision == "duplicate"
    assert second.result.state == "duplicate"
    assert second.result.external_references == first.result.external_references
