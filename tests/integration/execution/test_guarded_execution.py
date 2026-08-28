from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from tools.mock_mcp import MockProcurementMCPServer

from civitas.application.live_execution import PersistedApprovedExecutionAdapter
from civitas.application.plan_identity import approved_totals, selected_plan_hash
from civitas.approval.service import ApprovalService, ChangedPlanError
from civitas.contracts.claims import ClaimScope, TypedClaim
from civitas.contracts.enums import EvidenceOrigin, ExecutionState, FeasibilityStatus
from civitas.contracts.evidence import EvidenceIdentity, EvidenceRecord
from civitas.contracts.execution import ExecutionRequest
from civitas.contracts.mcp_product import ApprovedTotals, ExecuteApprovedPlanRequest
from civitas.contracts.tools import MCPToolCall, MCPToolResult
from civitas.execution.guarded import GuardedExecutionService, RefreshBundle, _load_plan
from civitas.integrations import (
    DEFAULT_EXECUTION_POLICY,
    ContextBoundExecutionMCPClient,
    ExecutionMCPClient,
    ExecutionProviderContext,
)
from civitas.persistence.database import Database
from civitas.persistence.models import (
    CandidatePlanModel,
    ExecutionAuditEventModel,
    ExecutionAuditModel,
    JuryDecisionModel,
    OrganizationModel,
    PlanningRunModel,
    ProcurementLineModel,
    ProviderWriteModel,
    SKUModel,
    SupplierModel,
    WarehouseModel,
)
from civitas.ports.identity import OperatorContext


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeIDs:
    def __init__(self) -> None:
        self._value = 0
        self._instance = uuid4().hex[:8]

    def new_id(self, namespace: str) -> str:
        self._value += 1
        return f"{namespace}-{self._instance}-{self._value}"


class StaticRefresher:
    def __init__(self, bundle: RefreshBundle) -> None:
        self._bundle = bundle

    async def refresh(self, **_: object) -> RefreshBundle:
        return self._bundle


class RejectedWriteServer(MockProcurementMCPServer):
    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        if call.tool_name == "create_procurement_order":
            return MCPToolResult(
                call_id=call.call_id,
                succeeded=False,
                observed_at=datetime(2026, 8, 27, 12, 0, tzinfo=UTC),
                payload={},
                error_code="provider_policy_rejected",
                error_message="provider rejected order",
            )
        return await super().invoke(call)


class CapturingExecutionConnections:
    def __init__(self, server: MockProcurementMCPServer) -> None:
        self._server = server
        self.contexts: list[ExecutionProviderContext] = []

    async def connect(self, context: ExecutionProviderContext) -> ContextBoundExecutionMCPClient:
        self.contexts.append(context)
        return ContextBoundExecutionMCPClient(
            client=ExecutionMCPClient(
                transport=self._server,
                policy=DEFAULT_EXECUTION_POLICY,
            ),
            execution_context=context,
        )


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def execution_request(
    *, planning_run_id: str, plan_id: str, jury_id: str, key: str
) -> ExecutionRequest:
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
                status="ready_for_approval",
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
                component_scores={
                    "critical_claim_coverage": 100,
                    "evidence_independence": 100,
                    "provenance_completeness": 100,
                    "evidence_freshness": 100,
                    "canonical_source_diversity": 100,
                    "contradiction_resolution": 100,
                    "dissent_robustness": 100,
                },
                integrity_score=Decimal("99"),
                gate_results=[
                    {"gate_code": gate_code, "passed": True, "reason_codes": []}
                    for gate_code in (
                        "solver_feasibility",
                        "hard_constraints",
                        "critical_contradictions",
                        "critical_external_support",
                        "execution_freshness",
                        "autonomy_bounds",
                        "human_approval",
                        "proposal_validity",
                        "dissent_completion",
                    )
                ],
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
            evidence=(
                evidence(
                    evidence_id="e-1", claim_id="claim-1", observed_at=now - timedelta(minutes=15)
                ),
            ),
            observed_unit_prices={offer_key: Decimal("5")},
            constraints={
                f"offer:{offer_key}:available": Decimal("10"),
                f"lead:{offer_key}:days": Decimal("0"),
            },
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
            constraints={
                f"offer:{offer_key}:available": Decimal("1"),
                f"lead:{offer_key}:days": Decimal("0"),
            },
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
            constraints={
                f"offer:{offer_key}:available": Decimal("10"),
                f"lead:{offer_key}:days": Decimal("0"),
            },
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
            constraints={
                f"offer:{offer_key}:available": Decimal("10"),
                f"lead:{offer_key}:days": Decimal("0"),
            },
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

    first, second = await asyncio.gather(
        service.execute(
            execution_request(
                planning_run_id=planning_run_id,
                plan_id=plan_id,
                jury_id=jury_id,
                key=key,
            )
        ),
        service.execute(
            execution_request(
                planning_run_id=planning_run_id,
                plan_id=plan_id,
                jury_id=jury_id,
                key=key,
            )
        ),
    )

    outcomes = {first.decision: first, second.decision: second}
    assert set(outcomes) == {"execute", "duplicate"}
    assert outcomes["execute"].result.state == "succeeded"
    assert outcomes["duplicate"].result.state == "duplicate"
    assert (
        outcomes["duplicate"].result.external_references
        == outcomes["execute"].result.external_references
    )
    assert len(server._write_results) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_and_jury_cannot_be_mixed_across_runs(database: Database) -> None:
    first = await seed_execution_state(database)
    second = await seed_execution_state(database)
    server = MockProcurementMCPServer()
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=server,
        ids=FakeIDs(),
        clock=FakeClock(datetime(2026, 8, 27, 12, 0, tzinfo=UTC)),
        refresher=StaticRefresher(
            RefreshBundle(claims=(), evidence=(), observed_unit_prices={}, constraints={})
        ),
    )

    with pytest.raises(ValueError, match="does not belong to the planning run"):
        await service.execute(
            execution_request(
                planning_run_id=first[0],
                plan_id=second[1],
                jury_id=second[2],
                key=identifier("idem"),
            )
        )

    assert server._write_results == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_hard_gate_cannot_reach_execution(database: Database) -> None:
    state = await seed_execution_state(database)
    async with database.unit_of_work() as uow:
        jury = await uow.require_session().get(JuryDecisionModel, state[2])
        assert jury is not None
        jury.gate_results = [
            {**gate, "passed": False} if gate["gate_code"] == "dissent_completion" else gate
            for gate in jury.gate_results
        ]

    server = MockProcurementMCPServer()
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=server,
        ids=FakeIDs(),
        clock=FakeClock(datetime(2026, 8, 27, 12, 0, tzinfo=UTC)),
        refresher=StaticRefresher(
            RefreshBundle(claims=(), evidence=(), observed_unit_prices={}, constraints={})
        ),
    )

    with pytest.raises(ValueError, match="failed hard gates"):
        await service.execute(
            execution_request(
                planning_run_id=state[0],
                plan_id=state[1],
                jury_id=state[2],
                key=identifier("idem"),
            )
        )

    assert server._write_results == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_idempotency_key_cannot_be_reused_for_different_action(database: Database) -> None:
    state = await seed_execution_state(database)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    offer_key = f"{state[6]}:{state[4]}:{state[5]}"
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=MockProcurementMCPServer(),
        ids=FakeIDs(),
        clock=FakeClock(now),
        refresher=StaticRefresher(
            RefreshBundle(
                claims=(
                    claim(
                        claim_id="claim-idempotency",
                        predicate="unit_price",
                        observed_at=now,
                        organization_id=state[3],
                        sku_id=state[4],
                        warehouse_id=state[5],
                    ),
                ),
                evidence=(
                    evidence(
                        evidence_id="e-idempotency",
                        claim_id="claim-idempotency",
                        observed_at=now,
                    ),
                ),
                observed_unit_prices={offer_key: Decimal("5")},
                constraints={
                    f"offer:{offer_key}:available": Decimal("10"),
                    f"lead:{offer_key}:days": Decimal("0"),
                },
            )
        ),
    )
    key = identifier("idem")
    original = execution_request(
        planning_run_id=state[0], plan_id=state[1], jury_id=state[2], key=key
    )
    await service.execute(original)

    with pytest.raises(ValueError, match="different execution request"):
        await service.execute(original.model_copy(update={"action": {"source": "tampered"}}))


def operator_context(organization_id: str) -> OperatorContext:
    return OperatorContext(
        organization_id=organization_id,
        operator_id="operator-approver",
        authentication_subject="oidc:test-approver",
        authenticated_at=datetime(2026, 8, 27, 11, 0, tzinfo=UTC),
        roles=("procurement-approver",),
    )


async def approved_receipt_for(
    database: Database,
    *,
    state: tuple[str, str, str, str, str, str, str],
    now: datetime,
) -> tuple[ApprovalService, OperatorContext, str]:
    async with database.sessions() as session:
        plan = await _load_plan(session, state[1])
    assert plan is not None
    context = operator_context(state[3])
    approvals = ApprovalService(
        sessions=database.sessions,
        ids=FakeIDs(),
        clock=FakeClock(now),
        secret_pepper=b"integration-test-pepper",
    )
    challenge = await approvals.issue(
        context=context,
        run_id=state[0],
        selected_plan_hash=selected_plan_hash(plan),
        policy_version="approval-v1",
        approved_totals=approved_totals(plan),
    )
    receipt = await approvals.approve(
        context=context,
        challenge_id=challenge.challenge_id,
        secret=challenge.challenge_secret,
    )
    return approvals, context, receipt.receipt_id


async def _selected_hash_for(database: Database, plan_id: str) -> str:
    async with database.sessions() as session:
        plan = await _load_plan(session, plan_id)
    assert plan is not None
    return selected_plan_hash(plan)


@pytest.mark.asyncio(loop_scope="session")
async def test_persisted_receipt_is_bound_to_execution_and_write_ledger(
    database: Database,
) -> None:
    state = await seed_execution_state(database)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    approvals, context, receipt_id = await approved_receipt_for(database, state=state, now=now)
    offer_key = f"{state[6]}:{state[4]}:{state[5]}"
    server = MockProcurementMCPServer()
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=server,
        ids=FakeIDs(),
        clock=FakeClock(now),
        approvals=approvals,
        refresher=StaticRefresher(
            RefreshBundle(
                claims=(
                    claim(
                        claim_id="claim-approved-receipt",
                        predicate="unit_price",
                        observed_at=now,
                        organization_id=state[3],
                        sku_id=state[4],
                        warehouse_id=state[5],
                    ),
                ),
                evidence=(
                    evidence(
                        evidence_id="e-approved-receipt",
                        claim_id="claim-approved-receipt",
                        observed_at=now,
                    ),
                ),
                observed_unit_prices={offer_key: Decimal("5")},
                constraints={
                    f"offer:{offer_key}:available": Decimal("10"),
                    f"lead:{offer_key}:days": Decimal("0"),
                },
            )
        ),
    )
    request = execution_request(
        planning_run_id=state[0], plan_id=state[1], jury_id=state[2], key=identifier("idem")
    )

    outcome = await service.execute(
        request,
        context=context,
        approval_receipt_id=receipt_id,
        write_mcp=ContextBoundExecutionMCPClient(
            client=ExecutionMCPClient(transport=server, policy=DEFAULT_EXECUTION_POLICY),
            execution_context=ExecutionProviderContext(
                execution_id=request.execution_id,
                approval_receipt_id=receipt_id,
                approved_plan_hash=(await _selected_hash_for(database, state[1])),
            ),
        ),
    )

    assert outcome.result.state is ExecutionState.SUCCEEDED
    async with database.sessions() as session:
        audit = await session.get(ExecutionAuditModel, request.execution_id)
        assert audit is not None
        assert audit.approval_receipt_id == receipt_id
        events = (
            await session.scalars(
                select(ExecutionAuditEventModel)
                .where(ExecutionAuditEventModel.execution_id == request.execution_id)
                .order_by(ExecutionAuditEventModel.sequence)
            )
        ).all()
        writes = (
            await session.scalars(
                select(ProviderWriteModel).where(
                    ProviderWriteModel.execution_id == request.execution_id
                )
            )
        ).all()
    assert [event.state for event in events] == ["pending", "succeeded"]
    assert len(writes) == 1
    assert writes[0].state == "succeeded"
    assert writes[0].external_reference


@pytest.mark.asyncio(loop_scope="session")
async def test_receipt_for_different_selected_plan_fails_before_provider_write(
    database: Database,
) -> None:
    state = await seed_execution_state(database)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    context = operator_context(state[3])
    approvals = ApprovalService(
        sessions=database.sessions,
        ids=FakeIDs(),
        clock=FakeClock(now),
        secret_pepper=b"integration-test-pepper",
    )
    challenge = await approvals.issue(
        context=context,
        run_id=state[0],
        selected_plan_hash="0" * 64,
        policy_version="approval-v1",
        approved_totals=ApprovedTotals(
            currency="USD",
            maximum_landed_cost=Decimal("100"),
            maximum_procurement_lines=10,
            maximum_distribution_lines=10,
        ),
    )
    receipt = await approvals.approve(
        context=context,
        challenge_id=challenge.challenge_id,
        secret=challenge.challenge_secret,
    )
    server = MockProcurementMCPServer()
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=server,
        ids=FakeIDs(),
        clock=FakeClock(now),
        approvals=approvals,
        refresher=StaticRefresher(
            RefreshBundle(claims=(), evidence=(), observed_unit_prices={}, constraints={})
        ),
    )

    with pytest.raises(ChangedPlanError):
        await service.execute(
            execution_request(
                planning_run_id=state[0],
                plan_id=state[1],
                jury_id=state[2],
                key=identifier("idem"),
            ),
            context=context,
            approval_receipt_id=receipt.receipt_id,
        )

    assert server._write_results == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_product_adapter_builds_provider_connection_from_persisted_receipt(
    database: Database,
) -> None:
    state = await seed_execution_state(database)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    approvals, context, receipt_id = await approved_receipt_for(database, state=state, now=now)
    offer_key = f"{state[6]}:{state[4]}:{state[5]}"
    server = MockProcurementMCPServer()
    connections = CapturingExecutionConnections(server)
    guarded = GuardedExecutionService(
        sessions=database.sessions,
        mcp=server,
        ids=FakeIDs(),
        clock=FakeClock(now),
        approvals=approvals,
        refresher=StaticRefresher(
            RefreshBundle(
                claims=(
                    claim(
                        claim_id="claim-product-binding",
                        predicate="unit_price",
                        observed_at=now,
                        organization_id=state[3],
                        sku_id=state[4],
                        warehouse_id=state[5],
                    ),
                ),
                evidence=(
                    evidence(
                        evidence_id="e-product-binding",
                        claim_id="claim-product-binding",
                        observed_at=now,
                    ),
                ),
                observed_unit_prices={offer_key: Decimal("5")},
                constraints={
                    f"offer:{offer_key}:available": Decimal("10"),
                    f"lead:{offer_key}:days": Decimal("0"),
                },
            )
        ),
    )
    adapter = PersistedApprovedExecutionAdapter(
        sessions=database.sessions,
        guarded=guarded,
        execution_connections=connections,
        ids=FakeIDs(),
        clock=FakeClock(now),
    )

    result = await adapter.execute(
        context=context,
        request=ExecuteApprovedPlanRequest(
            receipt_id=receipt_id,
            idempotency_key=identifier("product-idem"),
        ),
    )

    assert result.execution_state is ExecutionState.SUCCEEDED
    assert result.selected_plan_hash == await _selected_hash_for(database, state[1])
    assert len(connections.contexts) == 1
    assert connections.contexts[0].approval_receipt_id == receipt_id
    assert connections.contexts[0].approved_plan_hash == result.selected_plan_hash


@pytest.mark.asyncio(loop_scope="session")
async def test_unsuccessful_provider_result_is_never_recorded_as_success(
    database: Database,
) -> None:
    state = await seed_execution_state(database)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    offer_key = f"{state[6]}:{state[4]}:{state[5]}"
    service = GuardedExecutionService(
        sessions=database.sessions,
        mcp=RejectedWriteServer(),
        ids=FakeIDs(),
        clock=FakeClock(now),
        refresher=StaticRefresher(
            RefreshBundle(
                claims=(
                    claim(
                        claim_id="claim-provider-rejection",
                        predicate="unit_price",
                        observed_at=now,
                        organization_id=state[3],
                        sku_id=state[4],
                        warehouse_id=state[5],
                    ),
                ),
                evidence=(
                    evidence(
                        evidence_id="e-provider-rejection",
                        claim_id="claim-provider-rejection",
                        observed_at=now,
                    ),
                ),
                observed_unit_prices={offer_key: Decimal("5")},
                constraints={
                    f"offer:{offer_key}:available": Decimal("10"),
                    f"lead:{offer_key}:days": Decimal("0"),
                },
            )
        ),
    )
    request = execution_request(
        planning_run_id=state[0],
        plan_id=state[1],
        jury_id=state[2],
        key=identifier("provider-rejection"),
    )

    outcome = await service.execute(request)

    assert outcome.result.state is ExecutionState.COMPENSATION_REQUIRED
    assert outcome.result.failure_code == "provider_write_failed"
    async with database.sessions() as session:
        audit = await session.get(ExecutionAuditModel, request.execution_id)
        write = await session.scalar(
            select(ProviderWriteModel).where(
                ProviderWriteModel.execution_id == request.execution_id
            )
        )
    assert audit is not None
    assert audit.state == ExecutionState.COMPENSATION_REQUIRED.value
    assert write is not None
    assert write.state == ExecutionState.COMPENSATION_REQUIRED.value
