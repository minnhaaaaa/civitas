"""Atomic approval challenge lifecycle with no stored raw secret."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civitas.contracts.mcp_product import ApprovalChallenge, ApprovalReceipt, ApprovedTotals
from civitas.persistence.models import ApprovalChallengeModel, ApprovalReceiptModel
from civitas.persistence.repositories import ApprovalRepository
from civitas.ports.clock import Clock
from civitas.ports.identity import OperatorContext
from civitas.ports.ids import IDGenerator


class ApprovalError(RuntimeError):
    pass


class InvalidApprovalError(ApprovalError):
    pass


class ExpiredApprovalError(ApprovalError):
    pass


class ChangedPlanError(ApprovalError):
    pass


class ReplayApprovalError(ApprovalError):
    pass


class ApprovalService:
    """Issues and consumes challenges within the caller's organization boundary."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        ids: IDGenerator,
        clock: Clock,
        secret_pepper: bytes,
        ttl: timedelta = timedelta(minutes=10),
    ) -> None:
        if not secret_pepper:
            raise ValueError("secret_pepper is required")
        if ttl <= timedelta(0):
            raise ValueError("approval challenge ttl must be positive")
        self._sessions = sessions
        self._ids = ids
        self._clock = clock
        self._pepper = secret_pepper
        self._ttl = ttl

    async def issue(
        self,
        *,
        context: OperatorContext,
        run_id: str,
        selected_plan_hash: str,
        policy_version: str,
        approved_totals: ApprovedTotals,
    ) -> ApprovalChallenge:
        now = self._clock.now()
        secret = secrets.token_urlsafe(32)
        challenge = ApprovalChallenge(
            challenge_id=self._ids.new_id("approval-challenge"),
            challenge_secret=secret,
            organization_id=context.organization_id,
            operator_id=context.operator_id,
            run_id=run_id,
            selected_plan_hash=selected_plan_hash,
            policy_version=policy_version,
            approved_totals=approved_totals,
            issued_at=now,
            expires_at=now + self._ttl,
        )
        async with self._sessions() as session, session.begin():
            session.add(
                ApprovalChallengeModel(
                    id=challenge.challenge_id,
                    organization_id=challenge.organization_id,
                    operator_id=challenge.operator_id,
                    planning_run_id=challenge.run_id,
                    selected_plan_hash=challenge.selected_plan_hash,
                    policy_version=challenge.policy_version,
                    approved_totals=challenge.approved_totals.model_dump(mode="json"),
                    secret_hash=self._hash(secret),
                    issued_at=challenge.issued_at,
                    expires_at=challenge.expires_at,
                    state="pending",
                    invalidated_at=None,
                    invalidation_reason=None,
                )
            )
        return challenge

    async def approve(
        self, *, context: OperatorContext, challenge_id: str, secret: str
    ) -> ApprovalReceipt:
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            repository = ApprovalRepository(session)
            challenge = await repository.challenge_for_operator_for_update(
                challenge_id=challenge_id,
                organization_id=context.organization_id,
                operator_id=context.operator_id,
            )
            if challenge is None:
                raise InvalidApprovalError("approval challenge was not found for this operator")
            self._check_challenge(challenge, now, secret)
            receipt = await repository.receipt_for_challenge_for_update(challenge_id=challenge.id)
            if receipt is None:
                receipt = ApprovalReceiptModel(
                    id=self._ids.new_id("approval-receipt"),
                    challenge_id=challenge.id,
                    organization_id=challenge.organization_id,
                    operator_id=challenge.operator_id,
                    planning_run_id=challenge.planning_run_id,
                    selected_plan_hash=challenge.selected_plan_hash,
                    policy_version=challenge.policy_version,
                    approved_totals=challenge.approved_totals,
                    approved_at=now,
                    expires_at=challenge.expires_at,
                    consumed_at=None,
                    consumed_idempotency_key=None,
                )
                session.add(receipt)
                challenge.state = "approved"
            return _receipt_contract(receipt)

    async def consume_for_execution(
        self,
        *,
        context: OperatorContext,
        receipt_id: str,
        idempotency_key: str,
        selected_plan_hash: str,
        policy_version: str,
        actual_totals: ApprovedTotals,
    ) -> ApprovalReceipt:
        """Claim a receipt for exactly one idempotent action, atomically.

        Reusing the same key is allowed so a caller can safely retry after an
        ambiguous provider response; a distinct action is rejected.
        """
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            receipt = await ApprovalRepository(session).receipt_for_operator_for_update(
                receipt_id=receipt_id,
                organization_id=context.organization_id,
                operator_id=context.operator_id,
            )
            if receipt is None:
                raise InvalidApprovalError("approval receipt was not found for this operator")
            if receipt.expires_at <= now:
                raise ExpiredApprovalError("approval receipt has expired")
            if (
                receipt.selected_plan_hash != selected_plan_hash
                or receipt.policy_version != policy_version
            ):
                raise ChangedPlanError(
                    "approval receipt is not bound to the current plan and policy"
                )
            approved = ApprovedTotals.model_validate(receipt.approved_totals)
            if not _within_limits(actual_totals, approved):
                raise InvalidApprovalError("execution totals exceed approved limits")
            if receipt.consumed_idempotency_key not in (None, idempotency_key):
                raise ReplayApprovalError("approval receipt was already consumed by another action")
            if receipt.consumed_idempotency_key is None:
                receipt.consumed_idempotency_key = idempotency_key
                receipt.consumed_at = now
            return _receipt_contract(receipt)

    async def invalidate_plan(
        self, *, organization_id: str, run_id: str, selected_plan_hash: str
    ) -> int:
        """Invalidate outstanding challenges when selection changes materially."""
        now = self._clock.now()
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(ApprovalChallengeModel)
                .where(
                    ApprovalChallengeModel.organization_id == organization_id,
                    ApprovalChallengeModel.planning_run_id == run_id,
                    ApprovalChallengeModel.selected_plan_hash != selected_plan_hash,
                    ApprovalChallengeModel.state.in_(("pending", "approved")),
                )
                .values(
                    state="invalidated",
                    invalidated_at=now,
                    invalidation_reason="selected_plan_changed",
                )
            )
            return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def _check_challenge(
        self, challenge: ApprovalChallengeModel, now: datetime, secret: str
    ) -> None:
        if challenge.state == "invalidated":
            raise ChangedPlanError("approval challenge was invalidated by a material plan change")
        if challenge.expires_at <= now:
            challenge.state = "expired"
            raise ExpiredApprovalError("approval challenge has expired")
        if challenge.state not in {"pending", "approved"} or not hmac.compare_digest(
            challenge.secret_hash, self._hash(secret)
        ):
            raise InvalidApprovalError("approval challenge is invalid")

    def _hash(self, secret: str) -> str:
        return hmac.new(self._pepper, secret.encode("utf-8"), hashlib.sha256).hexdigest()


def _receipt_contract(row: ApprovalReceiptModel) -> ApprovalReceipt:
    return ApprovalReceipt(
        receipt_id=row.id,
        organization_id=row.organization_id,
        operator_id=row.operator_id,
        run_id=row.planning_run_id,
        selected_plan_hash=row.selected_plan_hash,
        policy_version=row.policy_version,
        approved_totals=ApprovedTotals.model_validate(row.approved_totals),
        approved_at=row.approved_at,
        expires_at=row.expires_at,
    )


def _within_limits(actual: ApprovedTotals, approved: ApprovedTotals) -> bool:
    return (
        actual.currency == approved.currency
        and actual.maximum_landed_cost <= approved.maximum_landed_cost
        and actual.maximum_procurement_lines <= approved.maximum_procurement_lines
        and actual.maximum_distribution_lines <= approved.maximum_distribution_lines
    )
