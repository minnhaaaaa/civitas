"""MCP SDK transports for explicitly configured local providers.

The implementation follows the MCP Python SDK v1 client lifecycle: create a
transport, enter a ``ClientSession``, initialize, then list or call tools.
Source: https://github.com/modelcontextprotocol/python-sdk/tree/v1.29.0
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ListToolsResult, TextContent

from civitas.contracts.provider_config import (
    HttpMCPTransport,
    ProviderDefinition,
    StdioMCPTransport,
)
from civitas.contracts.providers import (
    ProviderAccessContext,
    ProviderCapabilityManifest,
    ProviderToolCapability,
)
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.integrations.mcp import MCPAccessError, MCPInvocationError


class _ClientSession(Protocol):
    async def initialize(self) -> Any: ...

    async def list_tools(self) -> ListToolsResult: ...

    async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[_ClientSession]]


def stdio_server_parameters(
    provider: ProviderDefinition,
    context: ProviderAccessContext,
    environment: Mapping[str, str],
) -> StdioServerParameters:
    transport = provider.transport
    if not isinstance(transport, StdioMCPTransport):
        raise TypeError("provider is not configured for STDIO")
    child_environment: dict[str, str] = {}
    for child_name, local_reference in transport.credential_env_refs.get(context, {}).items():
        value = environment.get(local_reference)
        if value is None:
            raise MCPAccessError("provider credential reference is unavailable")
        child_environment[child_name] = value
    return StdioServerParameters(
        command=transport.command,
        args=list(transport.args),
        env=child_environment,
    )


class LocalMCPTransport:
    """One user-configured provider reached through the official MCP SDK."""

    def __init__(
        self,
        *,
        provider: ProviderDefinition,
        context: ProviderAccessContext,
        write_tool_names: frozenset[str] = frozenset(),
        environment: Mapping[str, str] | None = None,
        session_factory: SessionFactory | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._context = context
        self._write_tool_names = write_tool_names
        self._environment = os.environ if environment is None else environment
        self._session_factory = session_factory or self._open_sdk_session
        self._now = now_factory or (lambda: datetime.now(UTC))

    async def discover_capabilities(self) -> ProviderCapabilityManifest:
        async with self._session_factory() as session:
            initialized = await session.initialize()
            catalog = await session.list_tools()
        protocol_version = str(getattr(initialized, "protocol_version", "unknown"))
        capabilities = tuple(self._capability(tool) for tool in catalog.tools)
        return ProviderCapabilityManifest(
            provider_id=self._provider.provider_id,
            server_name=self._provider.provider_id,
            protocol_version=protocol_version,
            discovered_at=self._now(),
            tools=capabilities,
        )

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        is_configured_write = call.tool_name in self._write_tool_names
        if call.access_mode is MCPAccessMode.WRITE:
            if self._context is not ProviderAccessContext.EXECUTION:
                raise MCPAccessError("provider writes require the execution context")
            if not is_configured_write:
                raise MCPAccessError("tool is not configured as a provider write")
        elif is_configured_write:
            raise MCPAccessError("write tools cannot be invoked through read access")
        async with self._session_factory() as session:
            await session.initialize()
            sdk_result = await session.call_tool(call.tool_name, dict(call.arguments))
        if sdk_result.isError:
            return MCPToolResult(
                call_id=call.call_id,
                succeeded=False,
                observed_at=self._now(),
                payload={},
                error_code="provider_tool_error",
                error_message="provider tool returned an error",
            )
        try:
            payload = _result_payload(sdk_result)
            return MCPToolResult(
                call_id=call.call_id,
                succeeded=True,
                observed_at=self._now(),
                payload=payload,
            )
        except (TypeError, ValueError) as error:
            raise MCPInvocationError("provider returned an invalid structured payload") from error

    def _capability(self, tool: Any) -> ProviderToolCapability:
        access_mode = (
            MCPAccessMode.WRITE
            if tool.name in self._write_tool_names
            else MCPAccessMode.READ
        )
        annotations = getattr(tool, "annotations", None)
        idempotent = bool(
            access_mode is MCPAccessMode.WRITE
            and annotations is not None
            and getattr(annotations, "idempotentHint", False)
        )
        description = getattr(tool, "description", None)
        return ProviderToolCapability(
            name=tool.name,
            access_mode=access_mode,
            idempotent=idempotent,
            description=None if description is None else description[:500],
        )

    @asynccontextmanager
    async def _open_sdk_session(self) -> AsyncIterator[_ClientSession]:
        transport = self._provider.transport
        if isinstance(transport, StdioMCPTransport):
            parameters = stdio_server_parameters(
                self._provider,
                self._context,
                self._environment,
            )
            with Path(os.devnull).open("w", encoding="utf-8") as error_log:
                async with stdio_client(parameters, errlog=error_log) as streams:
                    async with ClientSession(streams[0], streams[1]) as session:
                        yield session
            return
        if isinstance(transport, HttpMCPTransport):
            headers = self._authorization_headers(transport)
            timeout = httpx.Timeout(10.0, read=30.0)
            async with (
                httpx.AsyncClient(
                    headers=headers,
                    follow_redirects=False,
                    timeout=timeout,
                ) as client,
                streamable_http_client(
                    str(transport.url),
                    http_client=client,
                    terminate_on_close=True,
                ) as streams,
                ClientSession(streams[0], streams[1]) as session,
            ):
                yield session
            return
        raise MCPAccessError("unsupported local MCP transport")

    def _authorization_headers(self, transport: HttpMCPTransport) -> dict[str, str]:
        reference = transport.authorization_env_refs.get(self._context)
        if reference is None:
            return {}
        value = self._environment.get(reference)
        if value is None:
            raise MCPAccessError("provider credential reference is unavailable")
        return {"Authorization": f"Bearer {value}"}


def _result_payload(result: CallToolResult) -> dict[str, object]:
    structured = result.structuredContent
    if structured is not None:
        return dict(structured)
    text_blocks = [item.text for item in result.content if isinstance(item, TextContent)]
    if len(text_blocks) != 1:
        raise ValueError("provider result must contain one JSON object")
    parsed = json.loads(text_blocks[0])
    if not isinstance(parsed, dict):
        raise ValueError("provider result must be a JSON object")
    return parsed
