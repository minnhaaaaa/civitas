from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from civitas.application.investigation import (
    DurableCleanRoomJury,
    EvidenceSnapshot,
    JuryDirectedInvestigator,
)
from civitas.contracts.claims import ClaimScope, TypedClaim
from civitas.contracts.enums import EvidenceOrigin, FeasibilityStatus, JuryState
from civitas.contracts.evidence import EvidenceIdentity, EvidenceRecord
from civitas.contracts.jury import JuryRequest
from civitas.contracts.optimization import CandidatePlan, OptimizationRequest
from civitas.contracts.providers import (
    OperationalEvidenceKind,
    OperationalObservation,
    ProviderEvidenceRead,
)
from civitas.contracts.tools import MCPToolResult
from civitas.workflow.models import WorkflowCheckpoint, WorkflowLimits, WorkflowPhase

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


class IDs:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self, namespace: str) -> str:
        self.value += 1
        return f"{namespace}-{self.value}"


class Clock:
    def now(self) -> datetime:
        return NOW


class Ledger:
    def __init__(self, snapshot: EvidenceSnapshot | None = None) -> None:
        self.snapshot = snapshot or EvidenceSnapshot()
        self.reads: list[ProviderEvidenceRead] = []
        self.dissent_phases: list[str] = []

    async def load(
        self,
        *,
        planning_run_id: str,
        claim_ids: tuple[str, ...] = (),
        evidence_ids: tuple[str, ...] = (),
    ) -> EvidenceSnapshot:
        del planning_run_id, claim_ids, evidence_ids
        return self.snapshot

    async def persist_read(
        self,
        *,
        planning_run_id: str,
        read: ProviderEvidenceRead,
        claims: tuple[TypedClaim, ...],
    ) -> EvidenceRecord:
        del planning_run_id
        self.reads.append(read)
        self.snapshot = EvidenceSnapshot(
            claims=(*self.snapshot.claims, *claims),
            evidence=(*self.snapshot.evidence, read.evidence),
        )
        return read.evidence

    async def record_dissent(
        self,
        *,
        planning_run_id: str,
        cycle_key: str,
        phase: str,
        payload: dict[str, object],
    ) -> None:
        del planning_run_id, cycle_key, payload
        self.dissent_phases.append(phase)


class Reader:
    def __init__(self, *, lead_time: int = 1) -> None:
        self.lead_time = lead_time
        self.calls: list[str] = []

    async def read(
        self,
        *,
        call: object,
        evidence_id: str,
        claim_ids: tuple[str, ...] = (),
        agent_id: str | None = None,
    ) -> ProviderEvidenceRead:
        self.calls.append(call.tool_name)
        if call.tool_name == "get_supplier_offers":
            payload = {
                "offers": [
                    {
                        "offer_id": "live-offer",
                        "supplier_id": "supplier-b",
                        "sku_id": "sku-1",
                        "destination_warehouse_id": "warehouse-1",
                        "arrival_bucket_id": "day-1",
                        "capacity": 10,
                        "unit_cost": 7,
                    }
                ],
                "observation_version": "live-v2",
            }
            observations = (
                OperationalObservation(
                    kind=OperationalEvidenceKind.SUPPLIER_OFFER,
                    subject="live-offer",
                    predicate="unit_price",
                    value=Decimal("7"),
                    unit="USD",
                    valid_at=NOW,
                    scope={"supplier_id": "supplier-b", "sku_id": "sku-1"},
                ),
            )
        else:
            payload = {
                "records": [{"supplier_id": "supplier-a", "lead_time_days": self.lead_time}],
                "observation_version": "live-v2",
            }
            observations = (
                OperationalObservation(
                    kind=OperationalEvidenceKind.LEAD_TIME,
                    subject="supplier-a",
                    predicate="lead_time",
                    value=Decimal(self.lead_time),
                    unit="day",
                    valid_at=NOW,
                    scope={"supplier_id": "supplier-a"},
                ),
            )
        result = MCPToolResult(
            call_id=call.call_id,
            succeeded=True,
            observed_at=NOW,
            payload=payload,
        )
        evidence = EvidenceRecord(
            evidence_id=evidence_id,
            claim_ids=claim_ids,
            identity=EvidenceIdentity(
                canonical_source_id=f"independent:{call.tool_name}",
                canonical_source_type="provider_audit",
                mcp_server=call.server_name,
                tool_name=call.tool_name,
                normalized_arguments=call.arguments,
                retrieved_at=NOW,
                observation_version="live-v2",
                raw_response_sha256=("b" if agent_id == "dissent" else "c") * 64,
            ),
            origin=EvidenceOrigin.EXTERNAL,
            agent_id=agent_id,
            content_summary="fresh provider evidence",
            raw_payload=payload,
        )
        return ProviderEvidenceRead(
            call=call,
            result=result,
            evidence=evidence,
            observations=observations,
        )


def request() -> OptimizationRequest:
    return OptimizationRequest(
        planning_run_id="run-1",
        input_data_version="inputs-v1",
        objectives_version="objectives-v1",
        constraints={
            "supplier_offers": [],
            "plan_annotations": {"plan-a": {"claim_ids": ["claim-old"], "evidence_ids": ["e-old"]}},
        },
    )


def baseline() -> EvidenceSnapshot:
    claim = TypedClaim(
        claim_id="claim-old",
        subject="supplier-a",
        predicate="lead_time",
        value=1,
        unit="day",
        valid_at=NOW,
        scope=ClaimScope(organization_id="org-1", supplier_id="supplier-a"),
        human_summary="Supplier A lead time is one day.",
    )
    evidence = EvidenceRecord(
        evidence_id="e-old",
        claim_ids=(claim.claim_id,),
        identity=EvidenceIdentity(
            canonical_source_id="supplier-master",
            canonical_source_type="supplier_api",
            mcp_server="provider",
            tool_name="get_lead_times",
            retrieved_at=NOW,
            observation_version="v1",
            raw_response_sha256="a" * 64,
        ),
        origin=EvidenceOrigin.EXTERNAL,
        content_summary="original lead-time evidence",
    )
    return EvidenceSnapshot(claims=(claim,), evidence=(evidence,))


@pytest.mark.asyncio
async def test_jury_feedback_becomes_persisted_solver_input_refresh() -> None:
    ledger = Ledger(baseline())
    reader = Reader()
    investigator = JuryDirectedInvestigator(
        reader=reader,
        ledger=ledger,
        ids=IDs(),
        server_name="provider",
        organization_id="org-1",
    )
    checkpoint = WorkflowCheckpoint(
        planning_run_id="run-1",
        phase=WorkflowPhase.INVESTIGATION,
        cycle=1,
        optimization_request=request(),
        investigation_backlog=("Verify the current supplier offer price.",),
    )

    outcome = await investigator.investigate(
        checkpoint,
        limits=WorkflowLimits(deadline_at=NOW + timedelta(hours=1), max_tool_calls=4),
    )

    assert reader.calls == ["get_supplier_offers"]
    assert ledger.reads and outcome.tool_calls_used == 1
    assert outcome.optimization_request.input_data_version != "inputs-v1"
    assert outcome.optimization_request.constraints["supplier_offers"][0]["offer_id"] == (
        "live-offer"
    )
    # Replanning receives facts only; procurement quantities remain an optimizer output.
    assert "quantity" not in outcome.optimization_request.constraints["supplier_offers"][0]


@pytest.mark.asyncio
async def test_clean_room_dissent_is_recorded_before_retrieval_and_can_invalidate() -> None:
    ledger = Ledger(baseline())
    jury = DurableCleanRoomJury(
        dissent_reader=Reader(lead_time=10),
        ledger=ledger,
        ids=IDs(),
        clock=Clock(),
        server_name="provider",
        organization_id="org-1",
        tool_budget=1,
    )
    candidate = CandidatePlan(
        plan_id="plan-a",
        planning_run_id="run-1",
        feasibility=FeasibilityStatus.FULLY_FEASIBLE,
        shortage_base_units=0,
        solver_version="solver-test",
    )

    evaluation = await jury.evaluate(
        JuryRequest(
            planning_run_id="run-1",
            candidate_plan=candidate,
            supporting_claim_ids=("claim-old",),
            evidence_ids=("e-old",),
            policy_version="decision-integrity-v1",
        )
    )

    assert ledger.dissent_phases == [
        "plan_recorded",
        "fresh_retrieval_complete",
        "comparison_complete",
    ]
    assert evaluation.state is JuryState.INVESTIGATE
    assert "CRITICAL_CONTRADICTION_UNRESOLVED" in evaluation.reason_codes
