"""Provider-neutral structured model interface."""

from typing import Protocol

from civitas.contracts.model import ModelRequest, ModelResponse


class ModelProvider(Protocol):
    async def complete(self, request: ModelRequest) -> ModelResponse: ...
