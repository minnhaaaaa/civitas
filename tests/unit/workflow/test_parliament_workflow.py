from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

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
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator
from civitas.workflow import ParliamentWorkflow, WorkflowLimits


class FakeClock(Clock):
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeIDs(IDGenerator):
    def __init__(self) -> None:
        self._value = 0

    def new_id(self, namespace: str) -> str:
        self._value += 1
        return f"{namespace}-{self._value}"


class FakeOptimizer:
    def __init__(self, results: list[OptimizationResult]) -> None:
        self._results = results
        self.calls = 0

    async def solve(self, request: OptimizationRequest) -> OptimizationResult:
        self.calls += 1
        index = min(self.calls - 1, len(self._results) - 1)
        return self._results[index]


class FakeJury:
    def __init__(self, evaluations: list[JuryEvaluation]) -> None:
        self._evaluations = evaluations
        self.calls: list[JuryRequest] = []

    async def evaluate(self, request: JuryRequest) -> JuryEvaluation:
        self.calls.append(request)
        index = min(len(self.calls) - 1, len(self._evaluations) - 1)
        return self._evaluations[index]


def _request(*, planning_run_id: str = "run-1", repeated: bool = False) -> OptimizationRequest:
    annotations: dict[str, Any] = {
        "plan-a": {"claim_ids": ["claim-a"], "evidence_ids": ["e1" if repeated else "e1"]},
        "plan-b": {"claim_ids": ["claim-b"], "evidence_ids": ["e1" if repeated else "e2"]},
    }
    return OptimizationRequest(
        planning_run_id=planning_run_id,
        input_data_version="inputs-v1",
        objectives_version="objectives-v1",
        constraints={"plan_annotations": annotations},
        maximum_alternatives=2,
    )


def _result(planning_run_id: str = "run-1") -> OptimizationResult:
    return OptimizationResult(
        planning_run_id=planning_run_id,
        alternatives=(
            CandidatePlan(
                plan_id="plan-a",
                planning_run_id=planning_run_id,
                feasibility=FeasibilityStatus.FULLY_FEASIBLE,
                shortage_base_units=0,
                metrics={
                    "fulfillment": Decimal("10"),
                    "critical_shortage": Decimal("0"),
                    "resilience": Decimal("8"),
                    "total_landed_cost": Decimal("15"),
                    "holding_cost": Decimal("3"),
                    "remaining_shelf_life": Decimal("6"),
                    "spoilage_exposure": Decimal("2"),
                    "expected_waste_value": Decimal("4"),
                    "lateness": Decimal("2"),
                    "redistribution_effort": Decimal("3"),
                    "capacity_slack": Decimal("8"),
                    "supplier_reliability": Decimal("9"),
                    "supplier_concentration": Decimal("4"),
                    "capacity_risk": Decimal("2"),
                    "expired_quantity": Decimal("1"),
                },
                solver_version="solver-1",
            ),
            CandidatePlan(
                plan_id="plan-b",
                planning_run_id=planning_run_id,
                feasibility=FeasibilityStatus.FULLY_FEASIBLE,
                shortage_base_units=0,
                metrics={
                    "fulfillment": Decimal("8"),
                    "critical_shortage": Decimal("1"),
                    "resilience": Decimal("6"),
                    "total_landed_cost": Decimal("10"),
                    "holding_cost": Decimal("2"),
                    "remaining_shelf_life": Decimal("9"),
                    "spoilage_exposure": Decimal("1"),
                    "expected_waste_value": Decimal("1"),
                    "lateness": Decimal("1"),
                    "redistribution_effort": Decimal("2"),
                    "capacity_slack": Decimal("6"),
                    "supplier_reliability": Decimal("7"),
                    "supplier_concentration": Decimal("2"),
                    "capacity_risk": Decimal("1"),
                    "expired_quantity": Decimal("0"),
                },
                solver_version="solver-1",
            ),
        ),
    )


def _evaluation(state: str, *, required: tuple[str, ...] = ()) -> JuryEvaluation:
    return JuryEvaluation(
        evaluation_id=f"eval-{state}",
        planning_run_id="run-1",
        plan_id="plan-a",
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
        state=state,
        reason_codes=(),
        required_investigation=required,
    )


@pytest.mark.asyncio
async def test_jury_investigation_reopens_planning() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result(), _result()]),
        jury=FakeJury(
            [_evaluation("investigate", required=("verify lead time",)), _evaluation("approve")]
        ),
        ids=FakeIDs(),
        clock=FakeClock(now),
    )
    checkpoint = workflow.start(planning_run_id="run-1", optimization_request=_request())

    result = await workflow.run(
        checkpoint,
        limits=WorkflowLimits(max_cycles=3, deadline_at=now + timedelta(hours=1)),
    )

    assert result.checkpoint.final_state == "approve"
    assert result.checkpoint.cycle == 2
    assert any(event["event_type"] == "investigation.requested" for event in result.events)


@pytest.mark.asyncio
async def test_repeated_evidence_detection_escalates_after_bound() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result(), _result()]),
        jury=FakeJury(
            [
                _evaluation("investigate", required=("refresh evidence",)),
                _evaluation("investigate", required=("refresh evidence",)),
            ]
        ),
        ids=FakeIDs(),
        clock=FakeClock(now),
    )

    result = await workflow.run(
        workflow.start(planning_run_id="run-1", optimization_request=_request(repeated=True)),
        limits=WorkflowLimits(
            max_cycles=3,
            max_repeated_evidence=0,
            deadline_at=now + timedelta(hours=1),
        ),
    )

    assert result.checkpoint.final_state == "escalate"
    assert result.checkpoint.repeated_evidence_hits >= 1


@pytest.mark.asyncio
async def test_exhausted_cycle_bound_escalates() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result()]),
        jury=FakeJury([_evaluation("investigate", required=("more checks",))]),
        ids=FakeIDs(),
        clock=FakeClock(now),
    )

    result = await workflow.run(
        workflow.start(planning_run_id="run-1", optimization_request=_request()),
        limits=WorkflowLimits(max_cycles=1, deadline_at=now + timedelta(hours=1)),
    )

    assert result.checkpoint.final_state == "escalate"


@pytest.mark.asyncio
async def test_checkpoint_resume_continues_from_mid_round() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result()]),
        jury=FakeJury([_evaluation("approve")]),
        ids=FakeIDs(),
        clock=FakeClock(now),
    )
    checkpoint = workflow.start(planning_run_id="run-1", optimization_request=_request())
    checkpoint, _ = await workflow.advance(
        checkpoint, limits=WorkflowLimits(max_cycles=3, deadline_at=now + timedelta(hours=1))
    )

    assert checkpoint.phase == "challenge"

    result = await workflow.run(
        checkpoint,
        limits=WorkflowLimits(max_cycles=3, deadline_at=now + timedelta(hours=1)),
    )

    assert result.checkpoint.final_state == "approve"


@pytest.mark.asyncio
async def test_every_transition_emits_a_typed_event() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result()]),
        jury=FakeJury([_evaluation("approve")]),
        ids=FakeIDs(),
        clock=FakeClock(now),
    )

    result = await workflow.run(
        workflow.start(planning_run_id="run-1", optimization_request=_request()),
        limits=WorkflowLimits(max_cycles=3, deadline_at=now + timedelta(hours=1)),
    )

    assert [event["sequence"] for event in result.events] == list(
        range(1, len(result.events) + 1)
    )
    assert all(isinstance(event["payload"], dict) and "phase" in event["payload"] for event in result.events)
    assert workflow.compile_langgraph() is not None


@pytest.mark.asyncio
async def test_agents_never_authorize_quantities_directly() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    workflow = ParliamentWorkflow(
        optimizer=FakeOptimizer([_result()]),
        jury=FakeJury([_evaluation("approve")]),
        ids=FakeIDs(),
        clock=FakeClock(now),
    )
    checkpoint, _ = await workflow.advance(
        workflow.start(planning_run_id="run-1", optimization_request=_request()),
        limits=WorkflowLimits(max_cycles=3, deadline_at=now + timedelta(hours=1)),
    )

    assert checkpoint.parliament is not None
    assert checkpoint.parliament.proposals
    assert all("quantity" not in proposal.model_dump() for proposal in checkpoint.parliament.proposals)
