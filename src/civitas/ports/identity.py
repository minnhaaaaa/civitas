"""Authenticated identity boundary for inbound adapters."""

from datetime import datetime
from typing import Protocol

from pydantic import Field

from civitas.contracts.common import Contract


class OperatorContext(Contract):
    """Organization and operator identity derived from an authenticated transport."""

    organization_id: str = Field(min_length=1, max_length=128)
    operator_id: str = Field(min_length=1, max_length=128)
    authentication_subject: str = Field(min_length=1, max_length=512)
    authenticated_at: datetime
    roles: tuple[str, ...] = Field(default=(), max_length=50)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)


class IdentityPort(Protocol):
    """Resolve identity from a transport-specific authenticated request."""

    async def current_operator(self) -> OperatorContext: ...
