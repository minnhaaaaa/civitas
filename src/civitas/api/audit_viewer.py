"""GET-only HTTP resources for signed audit-viewer capabilities."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from civitas.application.audit_viewer import (
    AuditCursorError,
    AuditLinkUnavailable,
    PostgreSQLAuditViewerService,
)
from civitas.identity.rate_limit import RateLimiter

_NO_STORE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def audit_viewer_routes(
    *, service: PostgreSQLAuditViewerService, rate_limiter: RateLimiter
) -> tuple[Route, ...]:
    async def manifest(request: Request) -> Response:
        return await _serve(
            request,
            rate_limiter,
            lambda token: service.manifest(token),
        )

    async def events(request: Request) -> Response:
        return await _serve(
            request,
            rate_limiter,
            lambda token: service.events(
                token,
                cursor=request.query_params.get("cursor"),
                page_size=_page_size(request),
            ),
        )

    async def evidence(request: Request) -> Response:
        return await _serve(
            request,
            rate_limiter,
            lambda token: service.evidence(
                token,
                cursor=request.query_params.get("cursor"),
                page_size=_page_size(request),
            ),
        )

    async def execution(request: Request) -> Response:
        return await _serve(
            request,
            rate_limiter,
            lambda token: service.execution_events(
                token,
                cursor=request.query_params.get("cursor"),
                page_size=_page_size(request),
            ),
        )

    return (
        Route("/api/audit/{token:str}/manifest", manifest, methods=["GET"]),
        Route("/api/audit/{token:str}/events", events, methods=["GET"]),
        Route("/api/audit/{token:str}/evidence", evidence, methods=["GET"]),
        Route("/api/audit/{token:str}/execution", execution, methods=["GET"]),
    )


async def _serve(
    request: Request,
    rate_limiter: RateLimiter,
    operation: Callable[[str], Awaitable[object]],
) -> Response:
    token = request.path_params["token"]
    token_key = hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()
    decision = await rate_limiter.acquire(f"audit-link:{token_key}")
    if not decision.allowed:
        return JSONResponse(
            {"detail": "audit view unavailable"},
            status_code=429,
            headers={**_NO_STORE_HEADERS, "Retry-After": str(decision.retry_after_seconds)},
        )
    try:
        result = await operation(token)
    except AuditLinkUnavailable:
        return JSONResponse(
            {"detail": "audit view unavailable"},
            status_code=404,
            headers=_NO_STORE_HEADERS,
        )
    except AuditCursorError as error:
        return JSONResponse({"detail": str(error)}, status_code=400, headers=_NO_STORE_HEADERS)
    model_dump = getattr(result, "model_dump", None)
    if not callable(model_dump):  # pragma: no cover - composition invariant
        raise TypeError("audit projection must be a contract")
    return JSONResponse(model_dump(mode="json"), headers=_NO_STORE_HEADERS)


def _page_size(request: Request) -> int:
    raw = request.query_params.get("limit", "25")
    try:
        return int(raw)
    except ValueError as error:
        raise AuditCursorError("page size must be an integer") from error
