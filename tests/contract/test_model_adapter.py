from __future__ import annotations

import json
from typing import cast
from urllib import request

import pytest

from civitas.contracts.model import ModelMessage, ModelRequest, ModelUsage
from civitas.integrations import (
    FakeModelAdapter,
    FakeModelPlan,
    GroqModelAdapter,
    ModelOutputValidationError,
    ModelResponseFormatError,
)


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.headers = cast(object, {})

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _request() -> ModelRequest:
    return ModelRequest(
        operation_id="op-1",
        messages=(ModelMessage(role="user", content="Return a supplier."),),
        output_schema={
            "type": "object",
            "properties": {
                "supplier_id": {"type": "string"},
                "quantity": {"type": "integer"},
            },
            "required": ["supplier_id", "quantity"],
            "additionalProperties": False,
        },
    )


@pytest.mark.asyncio
async def test_fake_model_adapter_validates_structured_output() -> None:
    adapter = FakeModelAdapter(
        {
            "op-1": FakeModelPlan(
                structured_output={"supplier_id": "S1"},
                usage=ModelUsage(input_tokens=1, output_tokens=1),
            )
        }
    )

    with pytest.raises(ModelOutputValidationError, match=r"\$\.quantity is required"):
        await adapter.complete(_request())


@pytest.mark.asyncio
async def test_groq_adapter_parses_and_validates_response() -> None:
    def opener(_request_obj: request.Request, *, timeout: float) -> _FakeHTTPResponse:
        assert timeout == 60
        return _FakeHTTPResponse(
            {
                "model": "groq-test",
                "choices": [
                    {
                        "message": {"content": json.dumps({"supplier_id": "S1", "quantity": 12})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 5},
            }
        )

    adapter = GroqModelAdapter(api_key="key", model_identifier="groq-test", opener=opener)
    response = await adapter.complete(_request())

    assert response.structured_output == {"supplier_id": "S1", "quantity": 12}
    assert response.usage == ModelUsage(input_tokens=7, output_tokens=5)
    assert response.model_identifier == "groq-test"


@pytest.mark.asyncio
async def test_groq_adapter_rejects_non_json_message_content() -> None:
    def opener(_request_obj: request.Request, *, timeout: float) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(
            {
                "choices": [{"message": {"content": "not-json"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    adapter = GroqModelAdapter(api_key="key", model_identifier="groq-test", opener=opener)

    with pytest.raises(ModelOutputValidationError, match="valid JSON"):
        await adapter.complete(_request())


@pytest.mark.asyncio
async def test_groq_adapter_requires_usage_metadata() -> None:
    def opener(_request_obj: request.Request, *, timeout: float) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {"content": json.dumps({"supplier_id": "S1", "quantity": 12})},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    adapter = GroqModelAdapter(api_key="key", model_identifier="groq-test", opener=opener)

    with pytest.raises(ModelResponseFormatError, match="usage metadata"):
        await adapter.complete(_request())
