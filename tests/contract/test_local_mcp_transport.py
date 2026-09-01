"""Contract tests for user-configured MCP SDK transports."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

from civitas.contracts.provider_config import ProviderDefinition, StdioMCPTransport
from civitas.contracts.providers import ProviderAccessContext
from civitas.contracts.tools import MCPAccessMode, MCPToolCall
from civitas.integrations.local_mcp import LocalMCPTransport, stdio_server_parameters
from civitas.integrations.mcp import MCPAccessError


class FakeSession:
    def __init__(self) -> None:
        self.called: tuple[str, dict[str, object]] | None = None

    async def initialize(self) -> SimpleNamespace:
        return SimpleNamespace(
            protocol_version="2025-06-18",
            server_info=SimpleNamespace(name="Warehouse Provider"),
        )

    async def list_tools(self) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name="get_stock",
                    description="Current stock",
                    inputSchema={"type": "object"},
                    annotations=ToolAnnotations(readOnlyHint=True),
                ),
                Tool(
                    name="submit_po",
                    description="Create an order",
                    inputSchema={"type": "object"},
                    annotations=ToolAnnotations(
                        readOnlyHint=False,
                        destructiveHint=True,
                        idempotentHint=True,
                    ),
                ),
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult:
        self.called = (name, arguments)
        return CallToolResult(content=[], structuredContent={"lots": []})


def _provider() -> ProviderDefinition:
    return ProviderDefinition(
        provider_id="warehouse",
        display_name="Warehouse MCP",
        enabled=True,
        transport=StdioMCPTransport(
            command="uvx",
            args=("warehouse-mcp", "--region", "north"),
            credential_env_refs={
                ProviderAccessContext.PLANNING: {
                    "WAREHOUSE_TOKEN": "WAREHOUSE_PLANNING_TOKEN"
                }
            },
        ),
    )


def test_stdio_parameters_pass_only_explicit_credential_environment() -> None:
    parameters = stdio_server_parameters(
        _provider(),
        ProviderAccessContext.PLANNING,
        {
            "WAREHOUSE_PLANNING_TOKEN": "planning-secret",
            "UNRELATED_PARENT_SECRET": "must-not-leak",
        },
    )

    assert parameters.command == "uvx"
    assert parameters.args == ["warehouse-mcp", "--region", "north"]
    assert parameters.env == {"WAREHOUSE_TOKEN": "planning-secret"}


def test_stdio_parameters_fail_when_a_credential_reference_is_missing() -> None:
    with pytest.raises(MCPAccessError, match="credential reference is unavailable"):
        stdio_server_parameters(_provider(), ProviderAccessContext.PLANNING, {})


@pytest.mark.asyncio
async def test_transport_discovers_typed_capabilities_and_invokes_structured_tool() -> None:
    session = FakeSession()

    @asynccontextmanager
    async def open_session() -> AsyncIterator[FakeSession]:
        yield session

    observed_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    transport = LocalMCPTransport(
        provider=_provider(),
        context=ProviderAccessContext.PLANNING,
        write_tool_names=frozenset({"submit_po"}),
        session_factory=open_session,
        now_factory=lambda: observed_at,
    )

    manifest = await transport.discover_capabilities()
    result = await transport.invoke(
        MCPToolCall(
            call_id="call-1",
            server_name="warehouse",
            tool_name="get_stock",
            arguments={"warehouse_id": "north"},
            access_mode=MCPAccessMode.READ,
        )
    )

    assert manifest.provider_id == "warehouse"
    assert manifest.protocol_version == "2025-06-18"
    assert manifest.tools[0].access_mode is MCPAccessMode.READ
    assert manifest.tools[1].access_mode is MCPAccessMode.WRITE
    assert manifest.tools[1].idempotent is True
    assert session.called == ("get_stock", {"warehouse_id": "north"})
    assert result.payload == {"lots": []}
    assert result.observed_at == observed_at


@pytest.mark.asyncio
async def test_planning_transport_rejects_write_before_opening_provider_session() -> None:
    opened = False

    @asynccontextmanager
    async def open_session() -> AsyncIterator[FakeSession]:
        nonlocal opened
        opened = True
        yield FakeSession()

    transport = LocalMCPTransport(
        provider=_provider(),
        context=ProviderAccessContext.PLANNING,
        write_tool_names=frozenset({"submit_po"}),
        session_factory=open_session,
    )

    with pytest.raises(MCPAccessError, match="execution context"):
        await transport.invoke(
            MCPToolCall(
                call_id="call-write",
                server_name="warehouse",
                tool_name="submit_po",
                arguments={"order": "approved"},
                access_mode=MCPAccessMode.WRITE,
                idempotency_key="execution-1:supplier-1",
            )
        )

    assert opened is False


@pytest.mark.asyncio
async def test_provider_error_content_is_not_reflected_in_public_error_message() -> None:
    class ErrorSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult:
            del name, arguments
            return CallToolResult(
                content=[TextContent(type="text", text="token=must-not-leak")],
                isError=True,
            )

    @asynccontextmanager
    async def open_session() -> AsyncIterator[ErrorSession]:
        yield ErrorSession()

    transport = LocalMCPTransport(
        provider=_provider(),
        context=ProviderAccessContext.PLANNING,
        session_factory=open_session,
    )
    result = await transport.invoke(
        MCPToolCall(
            call_id="call-error",
            server_name="warehouse",
            tool_name="get_stock",
            arguments={},
            access_mode=MCPAccessMode.READ,
        )
    )

    assert result.succeeded is False
    assert result.error_code == "provider_tool_error"
    assert result.error_message == "provider tool returned an error"
    assert "must-not-leak" not in result.model_dump_json()
