"""Translate a verified transport principal into application identity."""

from datetime import UTC, datetime

from pydantic import Field, field_validator

from civitas.contracts.common import Contract
from civitas.ports.identity import OperatorContext


class AuthenticatedPrincipal(Contract):
    """Claims after a transport adapter has verified authentication."""

    organization_id: str = Field(min_length=1, max_length=128)
    operator_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=512)
    authenticated_at: datetime
    roles: tuple[str, ...] = Field(default=(), max_length=50)

    @field_validator("authenticated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("authenticated_at must include a timezone offset")
        return value.astimezone(UTC)


def derive_operator_context(
    principal: AuthenticatedPrincipal, *, correlation_id: str | None = None
) -> OperatorContext:
    """Create the only identity object visible to product services.

    Adapters must verify tokens, sessions, or mTLS before constructing the
    principal.  Caller-provided organization/operator fields are never used.
    """

    return OperatorContext(
        organization_id=principal.organization_id,
        operator_id=principal.operator_id,
        authentication_subject=principal.subject,
        authenticated_at=principal.authenticated_at,
        roles=principal.roles,
        correlation_id=correlation_id,
    )
