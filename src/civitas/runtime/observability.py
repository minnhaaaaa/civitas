"""Low-cardinality metrics, trace propagation, and secret-safe JSON logs."""

from __future__ import annotations

import json
import logging
import re
import secrets
import sys
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from civitas.identity.audit import AuthenticationAuditEvent

_trace_id: ContextVar[str | None] = ContextVar("civitas_trace_id", default=None)
_TRACEPARENT = re.compile(
    r"^00-(?P<trace>[0-9a-f]{32})-(?P<span>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)
_BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
_URL_PASSWORD = re.compile(r"(?P<prefix>://[^:/\s]+:)[^@/\s]+@")
_SAFE_LOG_FIELDS = (
    "service",
    "environment",
    "event",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "correlation_id",
    "trace_id",
    "reason_code",
    "run_id",
    "execution_id",
    "worker_id",
)


def _utc_converter(timestamp: float | None) -> time.struct_time:
    return time.gmtime(timestamp)


def current_trace_id() -> str | None:
    return _trace_id.get()


def bind_trace_id(trace_id: str) -> Token[str | None]:
    return _trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    _trace_id.reset(token)


class JsonLogFormatter(logging.Formatter):
    converter = staticmethod(_utc_converter)

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "service": self._service,
            "environment": self._environment,
            "event": _redact(record.getMessage()),
        }
        trace_id = current_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
        for name in _SAFE_LOG_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = _redact(str(value))
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_logging(
    *,
    service: str,
    environment: str,
    level: str,
    log_format: str,
) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(JsonLogFormatter(service=service, environment=environment))
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


class LoggingAuthenticationAuditSink:
    """Emit transport authentication outcomes without identity or credential data."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("civitas.authentication")

    async def record(self, event: AuthenticationAuditEvent) -> None:
        self._logger.info(
            "authentication.request",
            extra={
                "method": event.method,
                "path": _bounded_route(event.path),
                "status_code": event.status_code,
                "correlation_id": event.correlation_id,
                "reason_code": event.outcome,
            },
        )


@dataclass(slots=True)
class MetricsRegistry:
    """In-process Prometheus projection without tenant or operator labels."""

    _requests: dict[tuple[str, str, int], int] = field(default_factory=dict)
    _durations: dict[tuple[str, str], tuple[int, float]] = field(default_factory=dict)
    _readiness: int = 0
    _lock: Lock = field(default_factory=Lock)

    def observe(self, *, method: str, path: str, status_code: int, duration: float) -> None:
        route = _bounded_route(path)
        with self._lock:
            request_key = (method.upper(), route, status_code)
            self._requests[request_key] = self._requests.get(request_key, 0) + 1
            duration_key = (method.upper(), route)
            count, total = self._durations.get(duration_key, (0, 0.0))
            self._durations[duration_key] = (count + 1, total + duration)

    def set_readiness(self, ready: bool) -> None:
        with self._lock:
            self._readiness = int(ready)

    def render(self) -> str:
        lines = [
            "# HELP civitas_ready Whether required runtime dependencies are ready.",
            "# TYPE civitas_ready gauge",
            f"civitas_ready {self._readiness}",
            "# HELP civitas_http_requests_total HTTP requests by bounded route and status.",
            "# TYPE civitas_http_requests_total counter",
        ]
        with self._lock:
            requests = sorted(self._requests.items())
            durations = sorted(self._durations.items())
        for (method, path, status), count in requests:
            labels = f'method="{method}",path="{path}",status="{status}"'
            lines.append(f"civitas_http_requests_total{{{labels}}} {count}")
        lines.extend(
            (
                "# HELP civitas_http_request_duration_seconds Request duration summary.",
                "# TYPE civitas_http_request_duration_seconds summary",
            )
        )
        for (method, path), (count, total) in durations:
            labels = f'method="{method}",path="{path}"'
            lines.append(f"civitas_http_request_duration_seconds_count{{{labels}}} {count}")
            lines.append(f"civitas_http_request_duration_seconds_sum{{{labels}}} {total:.6f}")
        return "\n".join(lines) + "\n"


class OperationalTelemetryMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: Any,
        *,
        metrics: MetricsRegistry,
        service: str,
        environment: str,
    ) -> None:
        super().__init__(app)
        self._metrics = metrics
        self._service = service
        self._environment = environment
        self._logger = logging.getLogger("civitas.http")

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        trace_id = _incoming_trace_id(request.headers.get("traceparent"))
        span_id = secrets.token_hex(8)
        token = bind_trace_id(trace_id)
        started = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - started
            self._metrics.observe(
                method=request.method,
                path=request.url.path,
                status_code=500,
                duration=duration,
            )
            self._log(request, 500, duration, trace_id)
            raise
        else:
            duration = time.perf_counter() - started
            response.headers["traceparent"] = f"00-{trace_id}-{span_id}-01"
            self._metrics.observe(
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration=duration,
            )
            self._log(request, response.status_code, duration, trace_id)
            return response
        finally:
            reset_trace_id(token)

    def _log(self, request: Request, status: int, duration: float, trace_id: str) -> None:
        self._logger.info(
            "http.request.completed",
            extra={
                "service": self._service,
                "environment": self._environment,
                "method": request.method,
                "path": _bounded_route(request.url.path),
                "status_code": status,
                "duration_ms": round(duration * 1000, 3),
                "correlation_id": request.headers.get("x-correlation-id"),
                "trace_id": trace_id,
            },
        )


def _incoming_trace_id(value: str | None) -> str:
    if value:
        match = _TRACEPARENT.fullmatch(value.casefold())
        if match and match.group("trace") != "0" * 32:
            return match.group("trace")
    return secrets.token_hex(16)


def _bounded_route(path: str) -> str:
    if path in {"/mcp", "/health/live", "/health/ready", "/metrics"}:
        return path
    return "other"


def _redact(value: str) -> str:
    redacted = _BEARER.sub("Bearer [REDACTED]", value)
    return _URL_PASSWORD.sub(r"\g<prefix>[REDACTED]@", redacted)
