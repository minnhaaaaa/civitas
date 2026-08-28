"""Production bearer authentication without retaining plaintext credentials."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import Field, field_validator

from civitas.contracts.common import Contract
from civitas.identity.context import AuthenticatedPrincipal, derive_operator_context
from civitas.ports.identity import OperatorContext


class BearerCredential(Contract):
    """A rotated opaque credential record loaded from a secret-backed registry.

    Only a SHA-256 digest is retained by the verifier.  The original bearer secret
    belongs in the deployment secret manager, never in an ORM row or audit event.
    """

    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    organization_id: str = Field(min_length=1, max_length=128)
    operator_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=512)
    roles: tuple[str, ...] = Field(default=(), max_length=50)
    not_before: datetime | None = None
    expires_at: datetime
    revoked: bool = False

    @field_validator("not_before", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("credential timestamps must include a timezone offset")
        return value.astimezone(UTC)

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, roles: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(role.strip().lower() for role in roles))
        if any(not role or len(role) > 64 for role in normalized):
            raise ValueError("roles must contain non-empty values of at most 64 characters")
        return normalized

    @classmethod
    def from_secret(
        cls,
        secret: str,
        *,
        organization_id: str,
        operator_id: str,
        subject: str,
        expires_at: datetime,
        roles: tuple[str, ...] = (),
        not_before: datetime | None = None,
    ) -> BearerCredential:
        if len(secret) < 32:
            raise ValueError("bearer credentials must contain at least 32 characters")
        return cls(
            token_sha256=_digest(secret),
            organization_id=organization_id,
            operator_id=operator_id,
            subject=subject,
            roles=roles,
            not_before=not_before,
            expires_at=expires_at,
        )


class BearerVerifier(Protocol):
    """Resolve an opaque bearer credential to verified transport identity."""

    async def verify(
        self, token: str, *, correlation_id: str | None = None
    ) -> OperatorContext | None: ...


class HashedBearerVerifier:
    """Constant-time, expiry-aware verifier for rotated opaque bearer tokens."""

    def __init__(self, credentials: Iterable[BearerCredential]) -> None:
        records = tuple(credentials)
        if not records:
            raise ValueError("at least one bearer credential is required")
        digests = [record.token_sha256 for record in records]
        if len(digests) != len(set(digests)):
            raise ValueError("bearer credential digests must be unique")
        self._credentials = records

    async def verify(
        self, token: str, *, correlation_id: str | None = None
    ) -> OperatorContext | None:
        # Always hash and scan the complete registry.  Do not expose whether a token
        # was unknown, expired, inactive, or revoked to the transport caller.
        candidate = _digest(token)
        matched: BearerCredential | None = None
        for credential in self._credentials:
            if hmac.compare_digest(candidate, credential.token_sha256):
                matched = credential
        if matched is None:
            return None
        now = datetime.now(UTC)
        if matched.revoked or matched.expires_at <= now:
            return None
        if matched.not_before is not None and matched.not_before > now:
            return None
        return derive_operator_context(
            AuthenticatedPrincipal(
                organization_id=matched.organization_id,
                operator_id=matched.operator_id,
                subject=matched.subject,
                authenticated_at=now,
                roles=matched.roles,
            ),
            correlation_id=correlation_id,
        )

    async def __call__(self, token: str) -> OperatorContext | None:
        """Retain compatibility with the original MCP resolver callback."""
        return await self.verify(token)

    async def resolve(self, token: str) -> OperatorContext | None:
        """Compatibility alias for composition roots using the resolver vocabulary."""
        return await self.verify(token)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
