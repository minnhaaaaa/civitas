"""PostgreSQL system-of-record adapter for claims, evidence, calls, and Dissent."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civitas.application.investigation import EvidenceSnapshot
from civitas.contracts.claims import ClaimScope, TypedClaim, ValidityInterval
from civitas.contracts.common import JsonObject
from civitas.contracts.enums import EvidenceOrigin
from civitas.contracts.evidence import EvidenceIdentity, EvidenceRecord
from civitas.contracts.providers import ProviderEvidenceRead
from civitas.persistence.models import (
    ClaimModel,
    DissentInvestigationModel,
    EvidenceClaimModel,
    EvidenceModel,
    LineageEdgeModel,
    MCPCallModel,
    PlanningRunModel,
    SourceModel,
)
from civitas.ports.ids import IDGenerator


class PostgreSQLEvidenceLedger:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        ids: IDGenerator,
    ) -> None:
        self._sessions = sessions
        self._ids = ids

    async def load(
        self,
        *,
        planning_run_id: str,
        claim_ids: Sequence[str] = (),
        evidence_ids: Sequence[str] = (),
    ) -> EvidenceSnapshot:
        async with self._sessions() as session:
            claim_statement = select(ClaimModel).where(
                ClaimModel.planning_run_id == planning_run_id
            )
            if claim_ids:
                claim_statement = claim_statement.where(ClaimModel.id.in_(claim_ids))
            claim_rows = (await session.scalars(claim_statement)).all()
            selected_claim_ids = tuple(row.id for row in claim_rows)
            evidence_statement = select(EvidenceModel).where(
                EvidenceModel.planning_run_id == planning_run_id
            )
            if evidence_ids or selected_claim_ids:
                linked_ids = select(EvidenceClaimModel.evidence_id).where(
                    EvidenceClaimModel.claim_id.in_(selected_claim_ids or ("",))
                )
                evidence_statement = evidence_statement.where(
                    or_(
                        EvidenceModel.id.in_(tuple(evidence_ids) or ("",)),
                        EvidenceModel.id.in_(linked_ids),
                    )
                )
            evidence_rows = (await session.scalars(evidence_statement)).all()
            source_ids = tuple({row.source_id for row in evidence_rows})
            call_ids = tuple({row.mcp_call_id for row in evidence_rows if row.mcp_call_id})
            sources = {
                row.id: row
                for row in (
                    await session.scalars(
                        select(SourceModel).where(SourceModel.id.in_(source_ids or ("",)))
                    )
                ).all()
            }
            calls = {
                row.id: row
                for row in (
                    await session.scalars(
                        select(MCPCallModel).where(MCPCallModel.id.in_(call_ids or ("",)))
                    )
                ).all()
            }
            evidence_claims = (
                await session.execute(
                    select(EvidenceClaimModel.evidence_id, EvidenceClaimModel.claim_id).where(
                        EvidenceClaimModel.evidence_id.in_(
                            tuple(row.id for row in evidence_rows) or ("",)
                        )
                    )
                )
            ).all()
            claim_ids_by_evidence: dict[str, list[str]] = {}
            for evidence_id, claim_id in evidence_claims:
                claim_ids_by_evidence.setdefault(evidence_id, []).append(claim_id)
            lineage = (
                await session.execute(
                    select(LineageEdgeModel.from_id, LineageEdgeModel.to_id).where(
                        LineageEdgeModel.planning_run_id == planning_run_id,
                        LineageEdgeModel.from_type == "evidence",
                        LineageEdgeModel.relationship == "DERIVED_FROM",
                    )
                )
            ).all()
            derived_by_evidence: dict[str, list[str]] = {}
            for child, parent in lineage:
                derived_by_evidence.setdefault(child, []).append(parent)
        return EvidenceSnapshot(
            claims=tuple(_claim_to_contract(row) for row in claim_rows),
            evidence=tuple(
                _evidence_to_contract(
                    row,
                    source=sources[row.source_id],
                    call=None if row.mcp_call_id is None else calls.get(row.mcp_call_id),
                    claim_ids=claim_ids_by_evidence.get(row.id, []),
                    derived_from=derived_by_evidence.get(row.id, []),
                )
                for row in evidence_rows
            ),
        )

    async def persist_read(
        self,
        *,
        planning_run_id: str,
        read: ProviderEvidenceRead,
        claims: Sequence[TypedClaim],
    ) -> EvidenceRecord:
        async with self._sessions() as session, session.begin():
            run = await session.get(PlanningRunModel, planning_run_id)
            if run is None:
                raise ValueError("planning run not found for evidence persistence")
            identity = read.evidence.identity
            source = await session.scalar(
                select(SourceModel).where(
                    SourceModel.organization_id == run.organization_id,
                    SourceModel.canonical_source_id == identity.canonical_source_id,
                )
            )
            if source is None:
                source = SourceModel(
                    id=self._ids.new_id("source"),
                    organization_id=run.organization_id,
                    canonical_source_id=identity.canonical_source_id,
                    canonical_source_type=identity.canonical_source_type,
                    upstream_dataset=identity.canonical_source_id,
                )
                session.add(source)
                await session.flush()
            call = await session.get(MCPCallModel, read.call.call_id)
            if call is None:
                call = MCPCallModel(
                    id=read.call.call_id,
                    planning_run_id=planning_run_id,
                    server=read.call.server_name,
                    tool_name=read.call.tool_name,
                    normalized_arguments=dict(read.call.arguments),
                    retrieved_at=read.result.observed_at,
                    response_sha256=identity.raw_response_sha256,
                    raw_response=dict(read.result.payload),
                )
                session.add(call)
            for claim in claims:
                if await session.get(ClaimModel, claim.claim_id) is None:
                    session.add(_claim_to_model(planning_run_id, claim))
            await session.flush()
            observation_version = identity.observation_version or ""
            existing = await session.scalar(
                select(EvidenceModel).where(
                    EvidenceModel.planning_run_id == planning_run_id,
                    EvidenceModel.source_id == source.id,
                    EvidenceModel.raw_response_sha256 == identity.raw_response_sha256,
                    EvidenceModel.observation_version == observation_version,
                )
            )
            actual = read.evidence
            if existing is None:
                existing = EvidenceModel(
                    id=actual.evidence_id,
                    planning_run_id=planning_run_id,
                    source_id=source.id,
                    mcp_call_id=call.id,
                    origin=actual.origin.value,
                    agent_id=actual.agent_id,
                    content_summary=actual.content_summary,
                    retrieved_at=identity.retrieved_at,
                    observation_version=observation_version,
                    raw_response_sha256=identity.raw_response_sha256,
                    raw_payload=None if actual.raw_payload is None else dict(actual.raw_payload),
                )
                session.add(existing)
                await session.flush()
            else:
                actual = actual.model_copy(update={"evidence_id": existing.id})
            for claim_id in actual.claim_ids:
                link = await session.get(EvidenceClaimModel, (existing.id, claim_id))
                if link is None:
                    session.add(EvidenceClaimModel(evidence_id=existing.id, claim_id=claim_id))
            for parent_id in actual.derived_from:
                session.add(
                    LineageEdgeModel(
                        id=self._ids.new_id("lineage"),
                        planning_run_id=planning_run_id,
                        from_type="evidence",
                        from_id=existing.id,
                        relationship="DERIVED_FROM",
                        to_type="evidence",
                        to_id=parent_id,
                    )
                )
            return actual

    async def record_dissent(
        self,
        *,
        planning_run_id: str,
        cycle_key: str,
        phase: str,
        payload: JsonObject,
    ) -> None:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(DissentInvestigationModel).where(
                    DissentInvestigationModel.planning_run_id == planning_run_id,
                    DissentInvestigationModel.cycle_key == cycle_key,
                    DissentInvestigationModel.phase == phase,
                )
            )
            if existing is None:
                session.add(
                    DissentInvestigationModel(
                        id=self._ids.new_id("dissent-audit"),
                        planning_run_id=planning_run_id,
                        cycle_key=cycle_key,
                        phase=phase,
                        payload=dict(payload),
                    )
                )


def _claim_to_model(planning_run_id: str, claim: TypedClaim) -> ClaimModel:
    interval = claim.valid_during
    payload = claim.model_dump(mode="json")
    return ClaimModel(
        id=claim.claim_id,
        planning_run_id=planning_run_id,
        organization_id=claim.scope.organization_id,
        sku_id=claim.scope.sku_id,
        warehouse_id=claim.scope.warehouse_id,
        supplier_id=claim.scope.supplier_id,
        subject=claim.subject,
        predicate=claim.predicate,
        value=payload["value"],
        unit=claim.unit,
        valid_at=claim.valid_at,
        valid_from=None if interval is None else interval.starts_at,
        valid_until=None if interval is None else interval.ends_at,
        scope=claim.scope.model_dump(mode="json"),
        human_summary=claim.human_summary,
        materiality="critical",
    )


def _claim_to_contract(row: ClaimModel) -> TypedClaim:
    interval = (
        None
        if row.valid_from is None or row.valid_until is None
        else ValidityInterval(starts_at=row.valid_from, ends_at=row.valid_until)
    )
    return TypedClaim(
        claim_id=row.id,
        subject=row.subject,
        predicate=row.predicate,
        value=row.value,
        unit=row.unit,
        valid_at=row.valid_at,
        valid_during=interval,
        scope=ClaimScope.model_validate(row.scope),
        human_summary=row.human_summary,
    )


def _evidence_to_contract(
    row: EvidenceModel,
    *,
    source: SourceModel,
    call: MCPCallModel | None,
    claim_ids: Sequence[str],
    derived_from: Sequence[str],
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=row.id,
        claim_ids=tuple(claim_ids),
        identity=EvidenceIdentity(
            canonical_source_id=source.canonical_source_id,
            canonical_source_type=source.canonical_source_type,
            mcp_server=None if call is None else call.server,
            tool_name=None if call is None else call.tool_name,
            normalized_arguments={} if call is None else call.normalized_arguments,
            retrieved_at=row.retrieved_at,
            observation_version=row.observation_version or None,
            raw_response_sha256=row.raw_response_sha256,
        ),
        origin=EvidenceOrigin(row.origin),
        agent_id=row.agent_id,
        content_summary=row.content_summary,
        derived_from=tuple(derived_from),
        raw_payload=row.raw_payload,
    )
