"""MCP boundary contracts.

Inbound and outbound MCP data is untrusted.  These contracts deliberately put
small, transport-independent limits around names and JSON payloads so an MCP
message cannot turn into an unbounded in-memory object before a policy checks
it.
"""

import json
from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator

from civitas.contracts.common import Contract, JsonObject, JsonValue

MAX_MCP_PAYLOAD_BYTES = 256 * 1024
MAX_MCP_JSON_DEPTH = 32
_MCP_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


def _validate_bounded_json(value: JsonObject, *, field_name: str) -> JsonObject:
    """Reject excessively deep or large untrusted MCP JSON before use."""

    if _json_depth(value) > MAX_MCP_JSON_DEPTH:
        raise ValueError(f"{field_name} may not exceed {MAX_MCP_JSON_DEPTH} JSON levels")
    try:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be JSON serializable") from error
    if len(encoded) > MAX_MCP_PAYLOAD_BYTES:
        raise ValueError(f"{field_name} may not exceed {MAX_MCP_PAYLOAD_BYTES} bytes")
    return value


def _json_depth(value: JsonValue) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 0


class MCPAccessMode(StrEnum):
    READ = "read"
    WRITE = "write"


class MCPToolCall(Contract):
    call_id: str = Field(min_length=1, max_length=128, pattern=_MCP_IDENTIFIER_PATTERN)
    server_name: str = Field(min_length=1, max_length=128, pattern=_MCP_IDENTIFIER_PATTERN)
    tool_name: str = Field(min_length=1, max_length=128, pattern=_MCP_IDENTIFIER_PATTERN)
    arguments: JsonObject
    access_mode: MCPAccessMode
    idempotency_key: str | None = Field(default=None, max_length=255)

    @field_validator("arguments")
    @classmethod
    def validate_arguments(cls, value: JsonObject) -> JsonObject:
        return _validate_bounded_json(value, field_name="arguments")


class MCPToolResult(Contract):
    call_id: str = Field(min_length=1, max_length=128, pattern=_MCP_IDENTIFIER_PATTERN)
    succeeded: bool
    observed_at: datetime
    payload: JsonObject
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    error_message: str | None = Field(default=None, max_length=500)

    @field_validator("payload")
    @classmethod
    def validate_payload(cls, value: JsonObject) -> JsonObject:
        return _validate_bounded_json(value, field_name="payload")
