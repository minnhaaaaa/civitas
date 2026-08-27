"""Provider-neutral model request and response contracts."""

from pydantic import Field

from civitas.contracts.common import Contract, JsonObject


class ModelMessage(Contract):
    role: str
    content: str


class ModelTool(Contract):
    name: str
    description: str
    input_schema: JsonObject


class ModelRequest(Contract):
    operation_id: str
    messages: tuple[ModelMessage, ...]
    output_schema: JsonObject
    tools: tuple[ModelTool, ...] = ()
    timeout_seconds: float = Field(default=60, gt=0)


class ModelUsage(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelResponse(Contract):
    operation_id: str
    structured_output: JsonObject
    usage: ModelUsage
    model_identifier: str
    finish_reason: str
