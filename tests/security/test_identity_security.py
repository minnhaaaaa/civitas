"""Adversarial tests for authenticated inbound identity and authorization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tests.contract.mcp_server.test_inbound_server import FakeService, _context

from civitas.identity import (
    BearerCredential,
    FixedWindowRateLimiter,
    HashedBearerVerifier,
    RoleAuthorizer,
)
from civitas.identity.audit import AuthenticationAuditEvent
from civitas.mcp_server import InboundMCPServer, StaticIdentityProvider


class RecordingAuditSink:
    def __init__(self) -> None:
        self.events: list[AuthenticationAuditEvent] = []

    async def record(self, event: AuthenticationAuditEvent) -> None:
        self.events.append(event)


def _credential(
    secret: str,
    *,
    expires_at: datetime | None = None,
    roles: tuple[str, ...] = ("procurement-viewer",),
) -> BearerCredential:
    return BearerCredential.from_secret(
        secret,
        organization_id="org-secure",
        operator_id="operator-secure",
        subject="opaque:operator-secure",
        roles=roles,
        expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_verifier_rejects_unknown_expired_and_revoked_credentials() -> None:
    valid_secret = "v" * 40
    expired_secret = "e" * 40
    revoked_secret = "r" * 40
    revoked = _credential(revoked_secret).model_copy(update={"revoked": True})
    verifier = HashedBearerVerifier(
        (
            _credential(valid_secret),
            _credential(expired_secret, expires_at=datetime.now(UTC) - timedelta(seconds=1)),
            revoked,
        )
    )

    assert await verifier.verify("unknown-secret-that-is-long-enough") is None
    assert await verifier.verify(expired_secret) is None
    assert await verifier.verify(revoked_secret) is None
    context = await verifier.verify(valid_secret, correlation_id="corr-valid")
    assert context is not None
    assert context.organization_id == "org-secure"
    assert context.correlation_id == "corr-valid"


@pytest.mark.asyncio
async def test_role_policy_blocks_privilege_escalation_before_service_call() -> None:
    service = FakeService()
    viewer = _context().model_copy(update={"roles": ("procurement-viewer",)})
    server = InboundMCPServer(
        service,
        StaticIdentityProvider(viewer),
        authorizer=RoleAuthorizer(),
    )

    result = await server.dispatch(
        "approve_execution",
        {"challenge_id": "challenge-1", "challenge_secret": "challenge-secret-123456"},
    )

    assert result["code"] == "rejected_execution"
    assert service.calls == []


@pytest.mark.asyncio
async def test_http_auth_propagates_correlation_audits_and_rate_limits() -> None:
    secret = "production-bearer-secret-with-more-than-32-characters"
    verifier = HashedBearerVerifier((_credential(secret),))
    audit = RecordingAuditSink()
    server = InboundMCPServer(FakeService(), StaticIdentityProvider(_context()))
    app = server.streamable_http_app(
        verifier=verifier,
        rate_limiter=FixedWindowRateLimiter(requests=1, window_seconds=60),
        audit_sink=audit,
    )
    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": f"Bearer {secret}",
        "X-Correlation-ID": "security-test-correlation",
    }

    async with (
        server.mcp.session_manager.run(),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        first = await client.post("/mcp", content=b"{}", headers=headers)
        second = await client.post("/mcp", content=b"{}", headers=headers)

    assert first.status_code != 401
    assert first.headers["x-correlation-id"] == "security-test-correlation"
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"
    assert [event.outcome for event in audit.events] == ["accepted", "rate_limited"]
    assert audit.events[0].operator_id == "operator-secure"


@pytest.mark.asyncio
async def test_http_boundary_rejects_correlation_header_injection() -> None:
    secret = "production-bearer-secret-with-more-than-32-characters"
    verifier = HashedBearerVerifier((_credential(secret),))
    server = InboundMCPServer(FakeService(), StaticIdentityProvider(_context()))
    transport = httpx.ASGITransport(app=server.streamable_http_app(verifier=verifier))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/mcp",
            content=b"{}",
            headers={
                "Authorization": f"Bearer {secret}",
                "X-Correlation-ID": "attempted newline injection\nforged-entry",
            },
        )

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_correlation_id"}
