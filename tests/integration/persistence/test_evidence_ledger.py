from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from civitas.contracts.claims import ClaimScope, TypedClaim
from civitas.contracts.enums import EvidenceOrigin
from civitas.contracts.evidence import EvidenceIdentity, EvidenceRecord
from civitas.contracts.providers import ProviderEvidenceRead
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.persistence.evidence import PostgreSQLEvidenceLedger
from civitas.persistence.models import (
    ClaimModel,
    DissentInvestigationModel,
    EvidenceClaimModel,
    EvidenceModel,
    MCPCallModel,
    OrganizationModel,
    PlanningRunModel,
    SourceModel,
)


class IDs:
    def new_id(self, namespace: str) -> str:
        return f"{namespace}-{uuid4().hex}"[:64]


@pytest.mark.asyncio
async def test_evidence_and_dissent_survive_adapter_restart(database: object) -> None:
    now = datetime.now(UTC)
    run_id = f"run-{uuid4().hex}"
    organization_id = f"org-{uuid4().hex}"
    claim_id = f"claim-{uuid4().hex}"
    evidence_id = f"evidence-{uuid4().hex}"
    source_id: str | None = None
    sessions = database.sessions  # type: ignore[attr-defined]
    async with sessions() as session, session.begin():
        session.add(OrganizationModel(id=organization_id, name="Evidence", timezone="UTC"))
        session.add(
            PlanningRunModel(
                id=run_id,
                organization_id=organization_id,
                horizon_start=now,
                horizon_end=now + timedelta(days=2),
                bucket_duration=timedelta(days=1),
                timezone="UTC",
                input_data_version="v1",
                status="created",
            )
        )
    call = MCPToolCall(
        call_id=f"call-{uuid4().hex}",
        server_name="provider",
        tool_name="get_lead_times",
        arguments={"organization_id": organization_id},
        access_mode=MCPAccessMode.READ,
    )
    result = MCPToolResult(
        call_id=call.call_id,
        succeeded=True,
        observed_at=now,
        payload={
            "records": [{"supplier_id": "external-supplier", "lead_time_days": 2}],
            "observation_version": "provider-v1",
        },
    )
    claim = TypedClaim(
        claim_id=claim_id,
        subject="external-supplier",
        predicate="lead_time",
        value=2,
        unit="day",
        valid_at=now,
        scope=ClaimScope(organization_id=organization_id),
        human_summary="Current lead time is two days.",
    )
    evidence = EvidenceRecord(
        evidence_id=evidence_id,
        claim_ids=(claim_id,),
        identity=EvidenceIdentity(
            canonical_source_id="provider-lead-time-master",
            canonical_source_type="supplier_api",
            mcp_server="provider",
            tool_name="get_lead_times",
            normalized_arguments=call.arguments,
            retrieved_at=now,
            observation_version="provider-v1",
            raw_response_sha256="d" * 64,
        ),
        origin=EvidenceOrigin.EXTERNAL,
        agent_id="dissent",
        content_summary="Fresh lead time.",
        raw_payload=result.payload,
    )
    read = ProviderEvidenceRead(call=call, result=result, evidence=evidence, observations=())
    try:
        ledger = PostgreSQLEvidenceLedger(sessions, ids=IDs())
        persisted = await ledger.persist_read(planning_run_id=run_id, read=read, claims=(claim,))
        await ledger.record_dissent(
            planning_run_id=run_id,
            cycle_key="dissent-cycle-1",
            phase="plan_recorded",
            payload={"checks": ["fresh:get_lead_times"], "read_only": True},
        )

        restarted = PostgreSQLEvidenceLedger(sessions, ids=IDs())
        snapshot = await restarted.load(
            planning_run_id=run_id,
            claim_ids=(claim_id,),
            evidence_ids=(evidence_id,),
        )

        assert persisted.evidence_id == evidence_id
        assert snapshot.claims == (claim,)
        assert snapshot.evidence[0].identity.canonical_source_id == ("provider-lead-time-master")
        async with sessions() as session:
            source_id = await session.scalar(
                select(EvidenceModel.source_id).where(EvidenceModel.id == evidence_id)
            )
            phases = (
                await session.scalars(
                    select(DissentInvestigationModel.phase).where(
                        DissentInvestigationModel.planning_run_id == run_id
                    )
                )
            ).all()
        assert phases == ["plan_recorded"]
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                delete(DissentInvestigationModel).where(
                    DissentInvestigationModel.planning_run_id == run_id
                )
            )
            await session.execute(
                delete(EvidenceClaimModel).where(EvidenceClaimModel.claim_id == claim_id)
            )
            await session.execute(
                delete(EvidenceModel).where(EvidenceModel.planning_run_id == run_id)
            )
            await session.execute(delete(ClaimModel).where(ClaimModel.id == claim_id))
            await session.execute(
                delete(MCPCallModel).where(MCPCallModel.planning_run_id == run_id)
            )
            await session.execute(delete(PlanningRunModel).where(PlanningRunModel.id == run_id))
            if source_id is not None:
                await session.execute(delete(SourceModel).where(SourceModel.id == source_id))
            await session.execute(
                delete(OrganizationModel).where(OrganizationModel.id == organization_id)
            )
