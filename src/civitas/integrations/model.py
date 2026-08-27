"""Provider-neutral model adapters and structured-output validation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from http.client import HTTPResponse
from json import JSONDecodeError
from typing import Protocol, cast
from urllib import error, request

from civitas.contracts.common import JsonObject
from civitas.contracts.model import ModelRequest, ModelResponse, ModelUsage
from civitas.ports.model_provider import ModelProvider


class ModelAdapterError(RuntimeError):
    """Base class for normalized model adapter failures."""

    code: str

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ModelTimeoutError(ModelAdapterError):
    def __init__(self, message: str = "Model request timed out.") -> None:
        super().__init__(message, code="timeout")


class ModelTransportError(ModelAdapterError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="transport_error")


class ModelResponseFormatError(ModelAdapterError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="response_format_error")


class ModelOutputValidationError(ModelAdapterError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="invalid_structured_output")


class SupportsHTTPResponse(Protocol):
    def __call__(self, request_obj: request.Request, *, timeout: float) -> HTTPResponse: ...


def _default_urlopen(request_obj: request.Request, *, timeout: float) -> HTTPResponse:
    return cast(HTTPResponse, request.urlopen(request_obj, timeout=timeout))


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    retryable_status_codes: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504})
    base_delay_seconds: float = 0.25

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("RetryPolicy attempts must be at least 1.")
        if self.base_delay_seconds < 0:
            raise ValueError("RetryPolicy base_delay_seconds must be non-negative.")


DEFAULT_RETRY_POLICY = RetryPolicy()


class GroqModelAdapter(ModelProvider):
    """Groq-backed provider that exposes Civitas contracts only."""

    def __init__(
        self,
        *,
        api_key: str,
        model_identifier: str,
        endpoint: str = "https://api.groq.com/openai/v1/chat/completions",
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        opener: SupportsHTTPResponse = _default_urlopen,
    ) -> None:
        self._api_key = api_key
        self._model_identifier = model_identifier
        self._endpoint = endpoint
        self._retry_policy = retry_policy
        self._opener = opener

    async def complete(self, request_contract: ModelRequest) -> ModelResponse:
        body = self._build_request_body(request_contract)
        response_payload = await self._post_with_retry(
            body,
            timeout=request_contract.timeout_seconds,
        )
        return self._parse_response(request_contract, response_payload)

    def _build_request_body(self, request_contract: ModelRequest) -> JsonObject:
        messages = [
            {"role": message.role, "content": message.content}
            for message in request_contract.messages
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in request_contract.tools
        ]
        response_format: JsonObject = {
            "type": "json_schema",
            "json_schema": {
                "name": f"{request_contract.operation_id}_response",
                "schema": request_contract.output_schema,
                "strict": True,
            },
        }
        payload: JsonObject = {
            "model": self._model_identifier,
            "messages": messages,
            "response_format": response_format,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    async def _post_with_retry(self, body: JsonObject, *, timeout: float) -> JsonObject:
        attempt = 0
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")

        while True:
            attempt += 1
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._post_once, encoded, timeout), timeout=timeout
                )
            except TimeoutError as exc:
                if attempt >= self._retry_policy.attempts:
                    raise ModelTimeoutError() from exc
                await asyncio.sleep(self._retry_policy.base_delay_seconds * attempt)
            except _RetryableHTTPError as exc:
                if attempt >= self._retry_policy.attempts:
                    raise ModelTransportError(
                        f"Groq request failed with HTTP {exc.status_code}: {exc.message}"
                    ) from exc
                await asyncio.sleep(self._retry_policy.base_delay_seconds * attempt)
            except error.HTTPError as exc:
                raise ModelTransportError(f"Groq request failed with HTTP {exc.code}.") from exc
            except error.URLError as exc:
                message = getattr(exc.reason, "strerror", None) or str(exc.reason)
                raise ModelTransportError(f"Groq request failed: {message}") from exc

    def _post_once(self, encoded: bytes, timeout: float) -> JsonObject:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        request_obj = request.Request(
            self._endpoint,
            data=encoded,
            headers=headers,
            method="POST",
        )
        try:
            response = self._opener(request_obj, timeout=timeout)
            response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            if exc.code in self._retry_policy.retryable_status_codes:
                raise _RetryableHTTPError(
                    status_code=exc.code,
                    message=_safe_http_error_message(exc),
                ) from exc
            raise
        try:
            payload = json.loads(response_body)
        except JSONDecodeError as exc:
            raise ModelResponseFormatError("Groq response was not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ModelResponseFormatError("Groq response JSON must be an object.")
        return cast(JsonObject, payload)

    def _parse_response(
        self, request_contract: ModelRequest, response_payload: JsonObject
    ) -> ModelResponse:
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelResponseFormatError("Groq response did not contain choices.")
        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise ModelResponseFormatError("Groq response choice must be an object.")
        message = first_choice.get("message")
        if not isinstance(message, Mapping):
            raise ModelResponseFormatError("Groq response choice did not contain a message.")
        content = message.get("content")
        if not isinstance(content, str):
            raise ModelResponseFormatError("Groq response message content must be a JSON string.")
        try:
            structured_output = json.loads(content)
        except JSONDecodeError as exc:
            raise ModelOutputValidationError("Model output was not valid JSON.") from exc
        _validate_against_schema(structured_output, request_contract.output_schema, path="$")
        usage_payload = response_payload.get("usage")
        usage = _parse_usage(usage_payload)
        finish_reason = first_choice.get("finish_reason")
        if not isinstance(finish_reason, str):
            finish_reason = "unknown"
        model_identifier = response_payload.get("model")
        if not isinstance(model_identifier, str):
            model_identifier = self._model_identifier
        return ModelResponse(
            operation_id=request_contract.operation_id,
            structured_output=cast(JsonObject, structured_output),
            usage=usage,
            model_identifier=model_identifier,
            finish_reason=finish_reason,
        )


@dataclass(frozen=True, slots=True)
class FakeModelPlan:
    structured_output: JsonObject
    usage: ModelUsage = field(
        default_factory=lambda: ModelUsage(input_tokens=0, output_tokens=0)
    )
    model_identifier: str = "fake-model"
    finish_reason: str = "stop"
    error: ModelAdapterError | None = None


class FakeModelAdapter(ModelProvider):
    """Deterministic model adapter for tests."""

    def __init__(self, responses: Mapping[str, FakeModelPlan]) -> None:
        self._responses = dict(responses)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        plan = self._responses.get(request.operation_id)
        if plan is None:
            raise ModelTransportError(
                f"No fake model response configured for {request.operation_id}."
            )
        if plan.error is not None:
            raise plan.error
        _validate_against_schema(plan.structured_output, request.output_schema, path="$")
        return ModelResponse(
            operation_id=request.operation_id,
            structured_output=plan.structured_output,
            usage=plan.usage,
            model_identifier=plan.model_identifier,
            finish_reason=plan.finish_reason,
        )


@dataclass(frozen=True, slots=True)
class _RetryableHTTPError(RuntimeError):
    status_code: int
    message: str


def _safe_http_error_message(exc: error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
    except Exception:
        return exc.reason if isinstance(exc.reason, str) else "request failed"
    return body[:200] if body else "request failed"


def _parse_usage(payload: object) -> ModelUsage:
    if not isinstance(payload, Mapping):
        raise ModelResponseFormatError("Groq response did not contain usage metadata.")
    prompt_tokens = payload.get("prompt_tokens")
    completion_tokens = payload.get("completion_tokens")
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        raise ModelResponseFormatError("Groq usage metadata was malformed.")
    return ModelUsage(input_tokens=prompt_tokens, output_tokens=completion_tokens)


def _validate_against_schema(instance: object, schema: object, *, path: str) -> None:
    if not isinstance(schema, Mapping):
        raise ModelOutputValidationError("Output schema must be a JSON object.")

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        matched = False
        last_error: ModelOutputValidationError | None = None
        for candidate in schema_type:
            try:
                _validate_against_schema(instance, {**schema, "type": candidate}, path=path)
                matched = True
                break
            except ModelOutputValidationError as exc:
                last_error = exc
        if not matched:
            raise last_error or ModelOutputValidationError(
                f"{path} did not match any allowed type."
            )
        return

    if schema_type == "object":
        if not isinstance(instance, Mapping):
            raise ModelOutputValidationError(f"{path} must be an object.")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ModelOutputValidationError(f"{path} properties must be an object.")
        required = schema.get("required", [])
        if not isinstance(required, Sequence) or isinstance(required, str):
            raise ModelOutputValidationError(f"{path} required must be an array.")
        for key in required:
            if not isinstance(key, str):
                raise ModelOutputValidationError(f"{path} required entries must be strings.")
            if key not in instance:
                raise ModelOutputValidationError(f"{path}.{key} is required.")
        allow_additional = schema.get("additionalProperties", True)
        if allow_additional is False:
            allowed = {key for key in properties if isinstance(key, str)}
            for key in instance:
                if key not in allowed:
                    raise ModelOutputValidationError(f"{path}.{key} is not allowed.")
        for key, value in instance.items():
            if key in properties:
                _validate_against_schema(value, properties[key], path=f"{path}.{key}")
        return

    if schema_type == "array":
        if not isinstance(instance, list):
            raise ModelOutputValidationError(f"{path} must be an array.")
        item_schema = schema.get("items")
        if item_schema is None:
            return
        for index, item in enumerate(instance):
            _validate_against_schema(item, item_schema, path=f"{path}[{index}]")
        return

    if schema_type == "string":
        if not isinstance(instance, str):
            raise ModelOutputValidationError(f"{path} must be a string.")
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and instance not in enum_values:
            raise ModelOutputValidationError(f"{path} must be one of {enum_values}.")
        return

    if schema_type == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise ModelOutputValidationError(f"{path} must be an integer.")
        return

    if schema_type == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            raise ModelOutputValidationError(f"{path} must be a number.")
        return

    if schema_type == "boolean":
        if not isinstance(instance, bool):
            raise ModelOutputValidationError(f"{path} must be a boolean.")
        return

    if schema_type == "null":
        if instance is not None:
            raise ModelOutputValidationError(f"{path} must be null.")
        return

    if "enum" in schema:
        enum_values = schema["enum"]
        if isinstance(enum_values, list) and instance not in enum_values:
            raise ModelOutputValidationError(f"{path} must be one of {enum_values}.")
        return

    if isinstance(instance, (dict, list, str, int, float, bool)) or instance is None:
        return

    raise ModelOutputValidationError(
        f"{path} contains a non-JSON value: {type(instance).__name__}."
    )
