"""Provider-neutral model request and response contracts."""

from pydantic import Field

from civitas.contracts.common import Contract, JsonObject


class ModelMessage(Contract):
    role: str = Field(min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=100_000)


class ModelTool(Contract):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    description: str = Field(max_length=4_000)
    input_schema: JsonObject


class ModelRequest(Contract):
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    messages: tuple[ModelMessage, ...]
    output_schema: JsonObject
    tools: tuple[ModelTool, ...] = ()
    timeout_seconds: float = Field(default=60, gt=0, le=300)


class ModelUsage(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelResponse(Contract):
    operation_id: str
    structured_output: JsonObject
    usage: ModelUsage
    model_identifier: str
    finish_reason: str
