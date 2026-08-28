"""Thin MCP adapters over the transport-neutral product-service port.

This module deliberately contains no planning, approval, or execution policy.
Every tool validates the public contract and delegates once to ``ProductService``.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, cast

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
from pydantic import ValidationError, create_model
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from civitas.contracts.mcp_product import (
    TOOL_REQUEST_CONTRACTS,
    TOOL_RESPONSE_CONTRACTS,
    ProductContract,
    ProductError,
    ProductErrorCode,
    ProductServiceError,
)
from civitas.identity.audit import (
    AuthenticationAuditSink,
    NullAuthenticationAuditSink,
    authentication_event,
    bind_audit_identity,
    reset_audit_identity,
)
from civitas.identity.authentication import BearerVerifier
from civitas.identity.authorization import RoleAuthorizer
from civitas.identity.rate_limit import RateLimiter
from civitas.ports.identity import OperatorContext
from civitas.ports.product_service import ProductService

MCP_SERVER_INSTRUCTIONS = """Civitas plans and executes procurement safely.

Safe sequence: call plan_procurement_goal, poll get_planning_run, inspect
get_decision_summary, then call prepare_execution. Present the returned immutable
challenge to the operator. Only after explicit approval call approve_execution,
then execute_approved_plan with its receipt and a new idempotency key. Never invent
quantities, an approval, a receipt, or execution success. Investigate or escalate
when Civitas reports that state instead of retrying around it.
"""

IdentityProvider = Callable[[], Awaitable[OperatorContext]]
_request_identity: ContextVar[OperatorContext | None] = ContextVar(
    "civitas_mcp_request_identity", default=None
)
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_OPERATIONAL_PATHS = frozenset({"/health/live", "/health/ready", "/metrics"})


class StaticIdentityProvider:
    """Explicit local-demo identity provider; suitable only for STDIO demos/tests."""

    def __init__(self, context: OperatorContext) -> None:
        self._context = context

    async def __call__(self) -> OperatorContext:
        return self._context


class BearerIdentityMiddleware(BaseHTTPMiddleware):
    """Require a resolved identity before the Streamable HTTP MCP app is reached."""

    def __init__(
        self,
        app: Any,
        resolve: Callable[[str], Awaitable[OperatorContext | None]] | None = None,
        verifier: BearerVerifier | None = None,
        rate_limiter: RateLimiter | None = None,
        audit_sink: AuthenticationAuditSink | None = None,
    ) -> None:
        super().__init__(app)
        if (resolve is None) == (verifier is None):
            raise ValueError("configure exactly one bearer identity resolver")
        self._resolve = resolve
        self._verifier = verifier
        self._rate_limiter = rate_limiter
        self._audit_sink = audit_sink or NullAuthenticationAuditSink()

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.method == "GET" and request.url.path in _OPERATIONAL_PATHS:
            return cast(Response, await call_next(request))
        correlation_id = request.headers.get("x-correlation-id") or secrets.token_urlsafe(18)
        if _CORRELATION_ID.fullmatch(correlation_id) is None:
            return JSONResponse({"error": "invalid_correlation_id"}, status_code=400)
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            await self._record(request, "rejected", correlation_id, 401)
            return JSONResponse(
                {"error": "unauthenticated"},
                status_code=401,
                headers={
                    "WWW-Authenticate": "Bearer",
                    "X-Correlation-ID": correlation_id,
                },
            )
        if self._verifier is not None:
            context = await self._verifier.verify(token, correlation_id=correlation_id)
        else:
            assert self._resolve is not None
            context = await self._resolve(token)
            if context is not None:
                context = context.model_copy(update={"correlation_id": correlation_id})
        if context is None:
            await self._record(request, "rejected", correlation_id, 401)
            return JSONResponse(
                {"error": "unauthenticated"},
                status_code=401,
                headers={
                    "WWW-Authenticate": "Bearer",
                    "X-Correlation-ID": correlation_id,
                },
            )
        if self._rate_limiter is not None:
            decision = await self._rate_limiter.acquire(
                f"{context.organization_id}:{context.operator_id}"
            )
            if not decision.allowed:
                await self._record(request, "rate_limited", correlation_id, 429, context)
                return JSONResponse(
                    {"error": "rate_limited"},
                    status_code=429,
                    headers={
                        "Retry-After": str(decision.retry_after_seconds),
                        "X-Correlation-ID": correlation_id,
                    },
                )
        reset_token = _request_identity.set(context)
        audit_token = bind_audit_identity(context)
        try:
            response = cast(Response, await call_next(request))
            response.headers["X-Correlation-ID"] = correlation_id
            await self._record(request, "accepted", correlation_id, response.status_code, context)
            return response
        finally:
            reset_audit_identity(audit_token)
            _request_identity.reset(reset_token)

    async def _record(
        self,
        request: Request,
        outcome: str,
        correlation_id: str,
        status_code: int,
        context: OperatorContext | None = None,
    ) -> None:
        await self._audit_sink.record(
            authentication_event(
                outcome=outcome,
                correlation_id=correlation_id,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                context=context,
            )
        )


class InboundMCPServer:
    """MCP server facade delegating all intent-level calls to one service port."""

    def __init__(
        self,
        service: ProductService,
        identity_provider: IdentityProvider,
        *,
        authorizer: RoleAuthorizer | None = None,
    ) -> None:
        self._service = service
        self._identity_provider = identity_provider
        self._authorizer = authorizer
        self._mcp = FastMCP(name="Civitas", instructions=MCP_SERVER_INSTRUCTIONS)
        for tool_name, request_type in TOOL_REQUEST_CONTRACTS.items():
            self._register_tool(tool_name, request_type)

    @property
    def mcp(self) -> FastMCP:
        """Expose the SDK server only to composition roots, never domain code."""
        return self._mcp

    async def dispatch(
        self,
        tool_name: str,
        payload: Mapping[str, object],
        *,
        context: OperatorContext | None = None,
    ) -> dict[str, object]:
        """Invoke a tool for SDK handlers and deterministic transport tests."""
        request_type = TOOL_REQUEST_CONTRACTS.get(tool_name)
        response_type = TOOL_RESPONSE_CONTRACTS.get(tool_name)
        if request_type is None or response_type is None:
            return self._error(ProductErrorCode.NOT_FOUND, "Unknown Civitas tool.", False)
        try:
            request = request_type.model_validate(payload)
        except ValidationError:
            return self._error(ProductErrorCode.INVALID_INPUT, "Invalid tool input.", False)

        caller = context or _request_identity.get()
        try:
            caller = caller or await self._identity_provider()
            if self._authorizer is not None and not self._authorizer.permits(caller, tool_name):
                return self._error(
                    ProductErrorCode.REJECTED_EXECUTION,
                    "The authenticated operator is not authorized for this operation.",
                    False,
                    correlation_id=caller.correlation_id,
                )
            operation = getattr(self._service, tool_name)
            result = await operation(caller, request)
            response = response_type.model_validate(result)
            return cast(dict[str, object], response.model_dump(mode="json"))
        except ProductServiceError as error:
            product_error = error.error
            if product_error.correlation_id is None and caller is not None:
                product_error = product_error.model_copy(
                    update={"correlation_id": caller.correlation_id}
                )
            return cast(dict[str, object], product_error.model_dump(mode="json"))
        except ValidationError:
            # A service returning a malformed contract is an internal conflict, not a leak.
            return self._error(
                ProductErrorCode.CONFLICT,
                "The operation could not be completed.",
                True,
                correlation_id=None if caller is None else caller.correlation_id,
            )
        except Exception:
            return self._error(
                ProductErrorCode.CONFLICT,
                "The operation could not be completed.",
                True,
                correlation_id=None if caller is None else caller.correlation_id,
            )

    def streamable_http_app(
        self,
        resolve_bearer_token: Callable[[str], Awaitable[OperatorContext | None]] | None = None,
        *,
        verifier: BearerVerifier | None = None,
        rate_limiter: RateLimiter | None = None,
        audit_sink: AuthenticationAuditSink | None = None,
    ) -> Starlette:
        """Return an authenticated Streamable HTTP app at the SDK's ``/mcp`` path."""
        app = self._mcp.streamable_http_app()
        app.add_middleware(
            BearerIdentityMiddleware,
            resolve=resolve_bearer_token,
            verifier=verifier,
            rate_limiter=rate_limiter,
            audit_sink=audit_sink,
        )
        return app

    def run_stdio(self) -> None:
        """Start the local Codex STDIO transport using the explicit demo identity."""
        self._mcp.run(transport="stdio")

    def _register_tool(self, tool_name: str, request_type: type[ProductContract]) -> None:
        # Bind the name now: a closure over the registration loop variable would
        # route every SDK callback to the final registered tool.
        def build_handler(bound_tool_name: str) -> Callable[..., Awaitable[dict[str, object]]]:
            async def handler(**payload: object) -> dict[str, object]:
                return await self.dispatch(bound_tool_name, payload)

            return handler

        self._mcp.add_tool(
            build_handler(tool_name),
            name=tool_name,
            description=f"Civitas intent-level operation: {tool_name}.",
            structured_output=False,
        )
        # FastMCP cannot infer a flattened Pydantic request from ``**payload``.
        # Reuse Agent 0's exact schema and validation model rather than maintaining
        # a duplicate SDK contract.
        tool = self._mcp._tool_manager._tools[tool_name]
        argument_model = create_model(
            f"{request_type.__name__}MCPArguments",
            __base__=(request_type, ArgModelBase),
        )
        tool.parameters = request_type.model_json_schema()
        tool.fn_metadata = FuncMetadata(arg_model=argument_model)

    @staticmethod
    def _error(
        code: ProductErrorCode,
        message: str,
        retryable: bool,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, object]:
        error = ProductError(
            code=code,
            message=message,
            retryable=retryable,
            occurred_at=datetime.now(UTC),
            correlation_id=correlation_id,
        )
        return cast(dict[str, object], error.model_dump(mode="json"))
