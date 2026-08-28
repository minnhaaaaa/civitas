"""Signed, immutable, organization-scoped projections for the optional audit viewer."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civitas.contracts.audit_viewer import (
    AuditClaimReference,
    AuditEventItem,
    AuditEventPage,
    AuditEvidenceItem,
    AuditEvidencePage,
    AuditExecutionEventItem,
    AuditExecutionEventPage,
    AuditExecutionSummary,
    AuditJuryCycle,
    AuditJuryGate,
    AuditManifest,
)
from civitas.contracts.enums import JuryState
from civitas.persistence.models import (
    AuditLinkModel,
    CandidatePlanModel,
    ClaimModel,
    EvidenceClaimModel,
    EvidenceModel,
    ExecutionAuditEventModel,
    ExecutionAuditModel,
    JuryDecisionModel,
    LineageEdgeModel,
    PlanningRunModel,
    SourceModel,
    WorkflowCheckpointModel,
    WorkflowEventModel,
)
from civitas.ports.clock import Clock
from civitas.ports.ids import IDGenerator

_LINK_DOMAIN = b"civitas-audit-link-v1\0"
_CURSOR_DOMAIN = b"civitas-audit-cursor-v1\0"
_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,128}$")


class AuditLinkUnavailable(ValueError):
    """Collapsed invalid, expired, revoked, or cross-scope capability result."""


class AuditCursorError(ValueError):
    """Raised when a resource cursor is invalid or belongs to another resource."""


@dataclass(frozen=True, slots=True)
class ResolvedAuditLink:
    link_id: str
    organization_id: str
    planning_run_id: str
    selected_plan_id: str
    maximum_event_sequence: int
    issued_at: datetime
    expires_at: datetime


class AuditCursorCodec:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("audit cursor secret must contain at least 32 bytes")
        self._secret = secret

    def encode(self, *, link_id: str, resource: str, after: int) -> str:
        body = json.dumps(
            {"v": 1, "link": link_id, "resource": resource, "after": after},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.digest(self._secret, _CURSOR_DOMAIN + body, "sha256")
        return f"{_b64(body)}.{_b64(signature)}"

    def decode(self, cursor: str | None, *, link_id: str, resource: str) -> int:
        if cursor is None:
            return 0
        try:
            body_text, signature_text = cursor.split(".", 1)
            body = _unb64(body_text)
            signature = _unb64(signature_text)
            payload = json.loads(body)
        except (ValueError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuditCursorError("invalid audit cursor") from error
        expected = hmac.digest(self._secret, _CURSOR_DOMAIN + body, "sha256")
        if not hmac.compare_digest(signature, expected):
            raise AuditCursorError("invalid audit cursor")
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("link") != link_id
            or payload.get("resource") != resource
        ):
            raise AuditCursorError("audit cursor belongs to another resource")
        after = payload.get("after")
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise AuditCursorError("invalid audit cursor position")
        return after


class PostgreSQLAuditViewerService:
    """Issue signed links and serve bounded projections without raw payloads or write paths."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        ids: IDGenerator,
        clock: Clock,
        secret: bytes,
        viewer_base_url: str,
        ttl: timedelta,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("audit link secret must contain at least 32 bytes")
        if not viewer_base_url.strip():
            raise ValueError("audit viewer base URL is required")
        if ttl <= timedelta(0) or ttl > timedelta(days=7):
            raise ValueError("audit link TTL must be positive and no longer than seven days")
        self._sessions = sessions
        self._ids = ids
        self._clock = clock
        self._secret = secret
        self._viewer_base_url = viewer_base_url.rstrip("/")
        self._ttl = ttl
        self._cursors = AuditCursorCodec(secret)

    async def issue(
        self,
        organization_id: str,
        run_id: str,
        selected_plan_id: str,
        maximum_event_sequence: int,
    ) -> str | None:
        now = self._clock.now()
        reference = secrets.token_urlsafe(24)
        token = f"{reference}.{_signature(self._secret, reference)}"
        async with self._sessions() as session, session.begin():
            plan = await session.scalar(
                select(CandidatePlanModel.id)
                .join(
                    PlanningRunModel,
                    PlanningRunModel.id == CandidatePlanModel.planning_run_id,
                )
                .where(
                    CandidatePlanModel.id == selected_plan_id,
                    CandidatePlanModel.planning_run_id == run_id,
                    PlanningRunModel.organization_id == organization_id,
                )
            )
            if plan is None:
                return None
            session.add(
                AuditLinkModel(
                    id=self._ids.new_id("audit-link"),
                    reference_hash=hashlib.sha256(reference.encode("ascii")).hexdigest(),
                    organization_id=organization_id,
                    planning_run_id=run_id,
                    selected_plan_id=selected_plan_id,
                    maximum_event_sequence=maximum_event_sequence,
                    issued_at=now,
                    expires_at=now + self._ttl,
                )
            )
        return f"{self._viewer_base_url}/audit/{quote(token, safe='.-_~')}"

    async def manifest(self, token: str) -> AuditManifest:
        link = await self._resolve(token)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(
                        PlanningRunModel,
                        WorkflowCheckpointModel,
                        CandidatePlanModel,
                    )
                    .join(
                        WorkflowCheckpointModel,
                        WorkflowCheckpointModel.planning_run_id == PlanningRunModel.id,
                    )
                    .join(
                        CandidatePlanModel,
                        CandidatePlanModel.planning_run_id == PlanningRunModel.id,
                    )
                    .where(
                        PlanningRunModel.id == link.planning_run_id,
                        PlanningRunModel.organization_id == link.organization_id,
                        CandidatePlanModel.id == link.selected_plan_id,
                    )
                )
            ).one_or_none()
            if row is None:
                raise AuditLinkUnavailable("audit link unavailable")
            run, checkpoint, plan = row._tuple()
            jury_rows = (
                await session.scalars(
                    select(JuryDecisionModel)
                    .where(
                        JuryDecisionModel.planning_run_id == link.planning_run_id,
                        JuryDecisionModel.plan_id == link.selected_plan_id,
                        JuryDecisionModel.calculated_at <= link.issued_at,
                    )
                    .order_by(JuryDecisionModel.calculated_at, JuryDecisionModel.id)
                )
            ).all()
            execution = await session.scalar(
                select(ExecutionAuditModel)
                .where(
                    ExecutionAuditModel.organization_id == link.organization_id,
                    ExecutionAuditModel.planning_run_id == link.planning_run_id,
                    ExecutionAuditModel.approved_plan_id == link.selected_plan_id,
                    ExecutionAuditModel.requested_at <= link.issued_at,
                )
                .order_by(ExecutionAuditModel.requested_at.desc())
                .limit(1)
            )
            execution_event_count = 0
            if execution is not None:
                execution_event_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ExecutionAuditEventModel)
                        .where(
                            ExecutionAuditEventModel.execution_id == execution.id,
                            ExecutionAuditEventModel.occurred_at <= link.issued_at,
                        )
                    )
                    or 0
                )
        goal = checkpoint.procurement_goal if isinstance(checkpoint.procurement_goal, dict) else {}
        objective = goal.get("objective")
        summary = (
            objective
            if isinstance(objective, str) and objective.strip()
            else "Solver-selected procurement decision with persisted evidence and Jury lineage."
        )
        return AuditManifest(
            run_id=run.id,
            selected_plan_id=plan.id,
            policy_version=checkpoint.policy_version or "decision-integrity-v1",
            title="Procurement decision record",
            summary=summary,
            captured_at=link.issued_at,
            link_expires_at=link.expires_at,
            maximum_event_sequence=link.maximum_event_sequence,
            jury=tuple(_jury_cycle(index, jury) for index, jury in enumerate(jury_rows, 1)),
            execution=_execution_summary(plan.id, execution, execution_event_count),
        )

    async def events(self, token: str, *, cursor: str | None, page_size: int) -> AuditEventPage:
        link = await self._resolve(token)
        _validate_page_size(page_size)
        after = self._cursors.decode(cursor, link_id=link.link_id, resource="events")
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(WorkflowEventModel)
                    .where(
                        WorkflowEventModel.planning_run_id == link.planning_run_id,
                        WorkflowEventModel.sequence > after,
                        WorkflowEventModel.sequence <= link.maximum_event_sequence,
                        WorkflowEventModel.occurred_at <= link.issued_at,
                    )
                    .order_by(WorkflowEventModel.sequence)
                    .limit(page_size + 1)
                )
            ).all()
        page = rows[:page_size]
        return AuditEventPage(
            items=tuple(_event_item(row) for row in page),
            next_cursor=(
                self._cursors.encode(
                    link_id=link.link_id, resource="events", after=page[-1].sequence
                )
                if len(rows) > page_size and page
                else None
            ),
        )

    async def evidence(
        self, token: str, *, cursor: str | None, page_size: int
    ) -> AuditEvidencePage:
        link = await self._resolve(token)
        _validate_page_size(page_size)
        offset = self._cursors.decode(cursor, link_id=link.link_id, resource="evidence")
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(EvidenceModel, SourceModel)
                    .join(SourceModel, SourceModel.id == EvidenceModel.source_id)
                    .where(
                        EvidenceModel.planning_run_id == link.planning_run_id,
                        EvidenceModel.retrieved_at <= link.issued_at,
                        SourceModel.organization_id == link.organization_id,
                    )
                    .order_by(EvidenceModel.retrieved_at, EvidenceModel.id)
                    .offset(offset)
                    .limit(page_size + 1)
                )
            ).all()
            page = rows[:page_size]
            evidence_ids = tuple(row[0].id for row in page)
            claim_rows = (
                await session.execute(
                    select(EvidenceClaimModel.evidence_id, ClaimModel)
                    .join(ClaimModel, ClaimModel.id == EvidenceClaimModel.claim_id)
                    .where(
                        EvidenceClaimModel.evidence_id.in_(evidence_ids or ("",)),
                        ClaimModel.organization_id == link.organization_id,
                        ClaimModel.planning_run_id == link.planning_run_id,
                    )
                    .order_by(ClaimModel.id)
                )
            ).all()
            lineage_rows = (
                await session.execute(
                    select(LineageEdgeModel.from_id, LineageEdgeModel.to_id).where(
                        LineageEdgeModel.planning_run_id == link.planning_run_id,
                        LineageEdgeModel.from_type == "evidence",
                        LineageEdgeModel.from_id.in_(evidence_ids or ("",)),
                        LineageEdgeModel.relationship == "DERIVED_FROM",
                    )
                )
            ).all()
        claims: dict[str, list[AuditClaimReference]] = {}
        for evidence_id, claim in claim_rows:
            claims.setdefault(evidence_id, []).append(
                AuditClaimReference(
                    claim_id=claim.id,
                    human_summary=claim.human_summary,
                    predicate=claim.predicate,
                    materiality=claim.materiality,
                )
            )
        derived: dict[str, list[str]] = {}
        for evidence_id, parent_id in lineage_rows:
            derived.setdefault(evidence_id, []).append(parent_id)
        return AuditEvidencePage(
            items=tuple(
                AuditEvidenceItem(
                    evidence_id=evidence.id,
                    content_summary=evidence.content_summary,
                    origin=evidence.origin,
                    source_group=source.canonical_source_id,
                    source_type=source.canonical_source_type,
                    retrieved_at=evidence.retrieved_at,
                    observation_version=evidence.observation_version or None,
                    claims=tuple(claims.get(evidence.id, ())),
                    derived_from=tuple(derived.get(evidence.id, ())),
                )
                for evidence, source in page
            ),
            next_cursor=(
                self._cursors.encode(
                    link_id=link.link_id,
                    resource="evidence",
                    after=offset + page_size,
                )
                if len(rows) > page_size
                else None
            ),
        )

    async def execution_events(
        self, token: str, *, cursor: str | None, page_size: int
    ) -> AuditExecutionEventPage:
        link = await self._resolve(token)
        _validate_page_size(page_size)
        after = self._cursors.decode(cursor, link_id=link.link_id, resource="execution")
        async with self._sessions() as session:
            execution_id = await session.scalar(
                select(ExecutionAuditModel.id)
                .where(
                    ExecutionAuditModel.organization_id == link.organization_id,
                    ExecutionAuditModel.planning_run_id == link.planning_run_id,
                    ExecutionAuditModel.approved_plan_id == link.selected_plan_id,
                    ExecutionAuditModel.requested_at <= link.issued_at,
                )
                .order_by(ExecutionAuditModel.requested_at.desc())
                .limit(1)
            )
            if execution_id is None:
                return AuditExecutionEventPage()
            rows = (
                await session.scalars(
                    select(ExecutionAuditEventModel)
                    .where(
                        ExecutionAuditEventModel.execution_id == execution_id,
                        ExecutionAuditEventModel.sequence > after,
                        ExecutionAuditEventModel.occurred_at <= link.issued_at,
                    )
                    .order_by(ExecutionAuditEventModel.sequence)
                    .limit(page_size + 1)
                )
            ).all()
        page = rows[:page_size]
        return AuditExecutionEventPage(
            items=tuple(
                AuditExecutionEventItem(
                    sequence=row.sequence,
                    occurred_at=row.occurred_at,
                    state=row.state,
                    reason_code=row.reason_code,
                    detail=_execution_event_detail(row.state, row.reason_code),
                )
                for row in page
            ),
            next_cursor=(
                self._cursors.encode(
                    link_id=link.link_id,
                    resource="execution",
                    after=page[-1].sequence,
                )
                if len(rows) > page_size and page
                else None
            ),
        )

    async def _resolve(self, token: str) -> ResolvedAuditLink:
        try:
            reference, signature = token.split(".", 1)
        except ValueError as error:
            raise AuditLinkUnavailable("audit link unavailable") from error
        if _REFERENCE_PATTERN.fullmatch(reference) is None or not hmac.compare_digest(
            signature, _signature(self._secret, reference)
        ):
            raise AuditLinkUnavailable("audit link unavailable")
        reference_hash = hashlib.sha256(reference.encode("ascii")).hexdigest()
        async with self._sessions() as session:
            row = await session.scalar(
                select(AuditLinkModel).where(AuditLinkModel.reference_hash == reference_hash)
            )
        now = self._clock.now()
        if row is None or row.revoked_at is not None or row.expires_at <= now:
            raise AuditLinkUnavailable("audit link unavailable")
        return ResolvedAuditLink(
            link_id=row.id,
            organization_id=row.organization_id,
            planning_run_id=row.planning_run_id,
            selected_plan_id=row.selected_plan_id,
            maximum_event_sequence=row.maximum_event_sequence,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
        )


def _jury_cycle(cycle: int, row: JuryDecisionModel) -> AuditJuryCycle:
    components = {
        str(name): float(value)
        for name, value in row.component_scores.items()
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    }
    gates = tuple(
        AuditJuryGate(
            gate_code=str(gate.get("gate_code", "unknown")),
            passed=gate.get("passed") is True,
            reason_codes=tuple(
                code for code in gate.get("reason_codes", ()) if isinstance(code, str)
            ),
        )
        for gate in row.gate_results
        if isinstance(gate, dict)
    )
    return AuditJuryCycle(
        cycle=cycle,
        state=JuryState(row.final_state),
        integrity_score=float(row.integrity_score),
        components=components,
        gates=gates,
        reason_codes=tuple(code for code in row.reason_codes if isinstance(code, str)),
    )


def _execution_summary(
    plan_id: str, execution: ExecutionAuditModel | None, event_count: int
) -> AuditExecutionSummary:
    if execution is None:
        return AuditExecutionSummary(
            approved_plan_id=plan_id,
            current_state="not_started",
            detail="No execution attempt existed when this immutable audit link was issued.",
            event_count=0,
        )
    detail = (
        "Guarded execution completed and remains recorded in the immutable ledger."
        if execution.state == "succeeded"
        else "Guarded execution state was captured from the immutable execution ledger."
    )
    return AuditExecutionSummary(
        approved_plan_id=plan_id,
        current_state=execution.state,
        detail=detail,
        event_count=event_count,
    )


def _event_item(row: WorkflowEventModel) -> AuditEventItem:
    nested = row.payload.get("event")
    payload = nested if isinstance(nested, dict) else row.payload
    reason_values = payload.get("reason_codes", ())
    reason_codes = (
        tuple(value for value in reason_values if isinstance(value, str))
        if isinstance(reason_values, (list, tuple))
        else ()
    )
    phase = payload.get("phase")
    return AuditEventItem(
        event_id=row.id,
        sequence=row.sequence,
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        phase=phase if isinstance(phase, str) else None,
        message=row.event_type.replace(".", " ").capitalize(),
        reason_codes=reason_codes,
    )


def _execution_event_detail(state: str, reason_code: str | None) -> str:
    if reason_code:
        return f"Execution recorded state {state} with reason code {reason_code}."
    return f"Execution recorded state {state}."


def _validate_page_size(page_size: int) -> None:
    if not 1 <= page_size <= 100:
        raise AuditCursorError("page size must be between 1 and 100")


def _signature(secret: bytes, reference: str) -> str:
    return _b64(hmac.digest(secret, _LINK_DOMAIN + reference.encode("ascii"), "sha256"))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
