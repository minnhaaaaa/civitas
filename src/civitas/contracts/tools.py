"""MCP boundary contracts."""

from datetime import datetime
from enum import StrEnum

from civitas.contracts.common import Contract, JsonObject


class MCPAccessMode(StrEnum):
    READ = "read"
    WRITE = "write"


class MCPToolCall(Contract):
    call_id: str
    server_name: str
    tool_name: str
    arguments: JsonObject
    access_mode: MCPAccessMode
    idempotency_key: str | None = None


class MCPToolResult(Contract):
    call_id: str
    succeeded: bool
    observed_at: datetime
    payload: JsonObject
    error_code: str | None = None
    error_message: str | None = None
