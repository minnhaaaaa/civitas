"""MCP invocation interface."""

from typing import Protocol

from civitas.contracts.tools import MCPToolCall, MCPToolResult


class MCPPort(Protocol):
    async def invoke(self, call: MCPToolCall) -> MCPToolResult: ...
