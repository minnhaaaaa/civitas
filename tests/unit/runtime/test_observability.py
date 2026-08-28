import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from civitas.identity.audit import AuthenticationAuditEvent
from civitas.mcp_server.server import BearerIdentityMiddleware
from civitas.runtime.observability import (
    JsonLogFormatter,
    LoggingAuthenticationAuditSink,
    MetricsRegistry,
    OperationalTelemetryMiddleware,
    bind_trace_id,
    reset_trace_id,
)


def test_json_logging_redacts_credentials_and_emits_trace_context() -> None:
    formatter = JsonLogFormatter(service="civitas-mcp", environment="production")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Bearer super-secret at postgresql://user:password@database/civitas",
        args=(),
        exc_info=None,
    )
    token = bind_trace_id("1" * 32)
    try:
        payload = json.loads(formatter.format(record))
    finally:
        reset_trace_id(token)

    assert payload["trace_id"] == "1" * 32
    assert "super-secret" not in payload["event"]
    assert "password" not in payload["event"]
    assert "[REDACTED]" in payload["event"]


def test_metrics_collapse_unbounded_paths_and_exclude_tenants() -> None:
    metrics = MetricsRegistry()
    metrics.observe(method="GET", path="/organizations/private", status_code=404, duration=0.2)
    metrics.observe(method="GET", path="/health/live", status_code=200, duration=0.1)

    rendered = metrics.render()

    assert 'path="other"' in rendered
    assert 'path="/health/live"' in rendered
    assert "private" not in rendered


@pytest.mark.asyncio
async def test_operational_routes_bypass_bearer_without_exposing_other_routes() -> None:
    async def response(request: Request) -> JSONResponse:
        del request
        return JSONResponse({"ok": True})

    async def reject(token: str) -> None:
        del token
        return None

    metrics = MetricsRegistry()
    app = Starlette(
        routes=(
            Route("/health/live", response),
            Route("/private", response),
        )
    )
    app.add_middleware(BearerIdentityMiddleware, resolve=reject)
    app.add_middleware(
        OperationalTelemetryMiddleware,
        metrics=metrics,
        service="civitas-mcp",
        environment="test",
    )
    trace_id = "1" * 32

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        live = await client.get(
            "/health/live",
            headers={"traceparent": f"00-{trace_id}-{'2' * 16}-01"},
        )
        private = await client.get("/private")

    assert live.status_code == 200
    assert live.headers["traceparent"].startswith(f"00-{trace_id}-")
    assert private.status_code == 401
    rendered = metrics.render()
    assert 'path="/health/live"' in rendered
    assert 'path="other"' in rendered


@pytest.mark.asyncio
async def test_authentication_log_sink_emits_only_safe_fields(caplog: object) -> None:
    event = AuthenticationAuditEvent(
        occurred_at=datetime.now(UTC),
        outcome="accepted",
        correlation_id="correlation-1",
        organization_id="private-org",
        operator_id="private-operator",
        authentication_subject="private-subject",
        method="POST",
        path="/mcp",
        status_code=200,
    )
    sink = LoggingAuthenticationAuditSink()

    with caplog.at_level(logging.INFO, logger="civitas.authentication"):  # type: ignore[attr-defined]
        await sink.record(event)

    record = caplog.records[-1]  # type: ignore[attr-defined]
    assert record.correlation_id == "correlation-1"
    assert not hasattr(record, "organization_id")
    assert not hasattr(record, "operator_id")
