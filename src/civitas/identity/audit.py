"""Request identity and correlation propagation for transport/application audits."""

from __future__ import annotations

from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Protocol

from pydantic import Field

from civitas.contracts.common import Contract
from civitas.ports.identity import OperatorContext

_audit_identity: ContextVar[OperatorContext | None] = ContextVar(
    "civitas_audit_identity", default=None
)


class AuthenticationAuditEvent(Contract):
    occurred_at: datetime
    outcome: str = Field(pattern=r"^(accepted|rejected|rate_limited)$")
    correlation_id: str
    organization_id: str | None = None
    operator_id: str | None = None
    authentication_subject: str | None = None
    method: str = Field(min_length=1, max_length=16)
    path: str = Field(min_length=1, max_length=512)
    status_code: int = Field(ge=100, le=599)


class AuthenticationAuditSink(Protocol):
    async def record(self, event: AuthenticationAuditEvent) -> None: ...


class NullAuthenticationAuditSink:
    async def record(self, event: AuthenticationAuditEvent) -> None:
        del event


def bind_audit_identity(context: OperatorContext) -> Token[OperatorContext | None]:
    return _audit_identity.set(context)


def reset_audit_identity(token: Token[OperatorContext | None]) -> None:
    _audit_identity.reset(token)


def current_audit_identity() -> OperatorContext | None:
    return _audit_identity.get()


def authentication_event(
    *,
    outcome: str,
    correlation_id: str,
    method: str,
    path: str,
    status_code: int,
    context: OperatorContext | None = None,
) -> AuthenticationAuditEvent:
    return AuthenticationAuditEvent(
        occurred_at=datetime.now(UTC),
        outcome=outcome,
        correlation_id=correlation_id,
        organization_id=None if context is None else context.organization_id,
        operator_id=None if context is None else context.operator_id,
        authentication_subject=None if context is None else context.authentication_subject,
        method=method,
        path=path,
        status_code=status_code,
    )
