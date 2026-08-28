from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete
from starlette.applications import Starlette

from civitas.api.audit_viewer import audit_viewer_routes
from civitas.application.audit_viewer import (
    AuditCursorError,
    AuditLinkUnavailable,
    PostgreSQLAuditViewerService,
)
from civitas.identity import FixedWindowRateLimiter
from civitas.mcp_server.server import BearerIdentityMiddleware
from civitas.persistence.models import (
    AuditLinkModel,
    CandidatePlanModel,
    ClaimModel,
    EvidenceClaimModel,
    EvidenceModel,
    JuryDecisionModel,
    OrganizationModel,
    PlanningRunModel,
    SourceModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
)


class IDs:
    def new_id(self, namespace: str) -> str:
        return f"{namespace}-{uuid4().hex}"


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


async def _seed(database: object, prefix: str, now: datetime) -> tuple[str, str, str]:
    organization_id = f"org-{prefix}"
    run_id = f"run-{prefix}"
    plan_id = f"plan-{prefix}"
    source_id = f"source-{prefix}"
    claim_ids = (f"claim-{prefix}-1", f"claim-{prefix}-2")
    evidence_ids = (f"evidence-{prefix}-1", f"evidence-{prefix}-2")
    sessions = database.sessions  # type: ignore[attr-defined]
    async with sessions() as session, session.begin():
        session.add(OrganizationModel(id=organization_id, name="Audit Org", timezone="UTC"))
        session.add(
            PlanningRunModel(
                id=run_id,
                organization_id=organization_id,
                horizon_start=now,
                horizon_end=now + timedelta(days=2),
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
                planning_run_id=run_id,
                stable_key=f"stable-{prefix}",
                feasibility="fully_feasible",
                shortage_base_units=0,
                metrics={"total_landed_cost": "42"},
                solver_version="solver-v1",
                selected=True,
            )
        )
        session.add(
            WorkflowCheckpointModel(
                planning_run_id=run_id,
                checkpoint={"private": "must-not-leak"},
                workflow_limits=None,
                procurement_goal={"objective": "Cover two-day demand without excess waste."},
                policy_version="decision-integrity-v1",
                phase="approve",
                cycle=1,
                event_sequence=2,
                completed=True,
                available_at=now,
                updated_at=now,
            )
        )
        await session.flush()
        session.add(
            JuryDecisionModel(
                id=f"jury-{prefix}",
                planning_run_id=run_id,
                plan_id=plan_id,
                policy_version="decision-integrity-v1",
                implementation_version="v1",
                calculated_at=now - timedelta(minutes=1),
                component_scores={"critical_claim_coverage": 95},
                integrity_score=Decimal("95"),
                gate_results=[{"gate_code": "critical-support", "passed": True}],
                final_state="approve",
                reason_codes=[],
                per_claim_contributions={},
            )
        )
        for sequence in (1, 2):
            session.add(
                WorkflowEventModel(
                    id=f"event-{prefix}-{sequence}",
                    planning_run_id=run_id,
                    sequence=sequence,
                    event_type="jury.evaluated" if sequence == 2 else "run.started",
                    occurred_at=now - timedelta(minutes=3 - sequence),
                    actor_id=None,
                    correlation_id="correlation-private",
                    causation_id=None,
                    schema_version="1",
                    payload={
                        "event": {"phase": "jury", "note": f"Public event {sequence}"},
                        "checkpoint": {"provider_secret": "must-not-leak"},
                    },
                )
            )
        session.add(
            SourceModel(
                id=source_id,
                organization_id=organization_id,
                canonical_source_id=f"canonical-{prefix}",
                canonical_source_type="supplier_registry",
                upstream_dataset="shared-upstream",
            )
        )
        await session.flush()
        for index, (claim_id, evidence_id) in enumerate(
            zip(claim_ids, evidence_ids, strict=True), 1
        ):
            session.add(
                ClaimModel(
                    id=claim_id,
                    planning_run_id=run_id,
                    organization_id=organization_id,
                    subject=f"supplier-{index}",
                    predicate="lead_time_days",
                    value=index,
                    unit="day",
                    valid_at=now,
                    valid_from=None,
                    valid_until=None,
                    scope={"organization_id": organization_id},
                    human_summary=f"Lead time evidence {index}",
                    materiality="critical",
                )
            )
            session.add(
                EvidenceModel(
                    id=evidence_id,
                    planning_run_id=run_id,
                    source_id=source_id,
                    mcp_call_id=None,
                    origin="external",
                    agent_id=None,
                    content_summary=f"Sanitized evidence {index}",
                    retrieved_at=now - timedelta(seconds=3 - index),
                    observation_version=f"v{index}",
                    raw_response_sha256=str(index) * 64,
                    raw_payload={"provider_credential": "must-not-leak"},
                )
            )
        await session.flush()
        for claim_id, evidence_id in zip(claim_ids, evidence_ids, strict=True):
            session.add(EvidenceClaimModel(evidence_id=evidence_id, claim_id=claim_id))
    return organization_id, run_id, plan_id


async def _cleanup(database: object, prefix: str) -> None:
    sessions = database.sessions  # type: ignore[attr-defined]
    run_id = f"run-{prefix}"
    organization_id = f"org-{prefix}"
    async with sessions() as session, session.begin():
        await session.execute(
            delete(EvidenceClaimModel).where(
                EvidenceClaimModel.evidence_id.in_((f"evidence-{prefix}-1", f"evidence-{prefix}-2"))
            )
        )
        await session.execute(
            delete(AuditLinkModel).where(AuditLinkModel.organization_id == organization_id)
        )
        await session.execute(delete(EvidenceModel).where(EvidenceModel.planning_run_id == run_id))
        await session.execute(delete(ClaimModel).where(ClaimModel.planning_run_id == run_id))
        await session.execute(
            delete(SourceModel).where(SourceModel.organization_id == organization_id)
        )
        await session.execute(
            delete(WorkflowEventModel).where(WorkflowEventModel.planning_run_id == run_id)
        )
        await session.execute(
            delete(JuryDecisionModel).where(JuryDecisionModel.planning_run_id == run_id)
        )
        await session.execute(
            delete(WorkflowCheckpointModel).where(WorkflowCheckpointModel.planning_run_id == run_id)
        )
        await session.execute(
            delete(CandidatePlanModel).where(CandidatePlanModel.planning_run_id == run_id)
        )
        await session.execute(delete(PlanningRunModel).where(PlanningRunModel.id == run_id))
        await session.execute(
            delete(OrganizationModel).where(OrganizationModel.id == organization_id)
        )


@pytest.mark.asyncio
async def test_signed_audit_resources_are_scoped_paginated_and_sanitized(database: object) -> None:
    prefix = uuid4().hex
    other = uuid4().hex
    now = datetime.now(UTC)
    organization_id, run_id, plan_id = await _seed(database, prefix, now)
    await _seed(database, other, now)
    clock = MutableClock(now)
    service = PostgreSQLAuditViewerService(
        sessions=database.sessions,  # type: ignore[attr-defined]
        ids=IDs(),
        clock=clock,
        secret=b"signed-audit-secret-for-tests-123456",
        viewer_base_url="https://audit.example",
        ttl=timedelta(minutes=15),
    )
    try:
        link = await service.issue(organization_id, run_id, plan_id, 2)
        assert link is not None
        token = link.rsplit("/", 1)[-1]
        assert organization_id not in token and run_id not in token and plan_id not in token
        assert await service.issue(organization_id, f"run-{other}", f"plan-{other}", 2) is None

        manifest = await service.manifest(token)
        first_events = await service.events(token, cursor=None, page_size=1)
        second_events = await service.events(token, cursor=first_events.next_cursor, page_size=1)
        first_evidence = await service.evidence(token, cursor=None, page_size=1)
        second_evidence = await service.evidence(
            token, cursor=first_evidence.next_cursor, page_size=1
        )

        assert manifest.run_id == run_id and manifest.selected_plan_id == plan_id
        assert [item.sequence for item in first_events.items + second_events.items] == [1, 2]
        assert len(first_evidence.items + second_evidence.items) == 2
        serialized = " ".join(
            (
                manifest.model_dump_json(),
                first_events.model_dump_json(),
                first_evidence.model_dump_json(),
            )
        )
        assert "must-not-leak" not in serialized
        assert "provider_credential" not in serialized
        assert "correlation-private" not in serialized
        with pytest.raises(AuditCursorError):
            await service.evidence(token, cursor=first_events.next_cursor, page_size=1)
        with pytest.raises(AuditLinkUnavailable):
            await service.manifest(token[:-1] + ("A" if token[-1] != "A" else "B"))

        app = Starlette(
            routes=audit_viewer_routes(
                service=service,
                rate_limiter=FixedWindowRateLimiter(requests=20, window_seconds=60),
            )
        )

        async def reject_bearer(value: str) -> None:
            del value
            return None

        app.add_middleware(BearerIdentityMiddleware, resolve=reject_bearer)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(f"/api/audit/{token}/manifest")
            invalid = await client.get(f"/api/audit/{token}A/manifest")
            post = await client.post(f"/api/audit/{token}/manifest")
        assert response.status_code == 200
        assert response.headers["cache-control"].startswith("private, no-store")
        assert response.headers["referrer-policy"] == "no-referrer"
        assert invalid.status_code == 404
        assert post.status_code == 401

        clock.value = now + timedelta(minutes=16)
        with pytest.raises(AuditLinkUnavailable):
            await service.manifest(token)
    finally:
        await _cleanup(database, prefix)
        await _cleanup(database, other)
