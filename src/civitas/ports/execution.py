"""Idempotent execution interface."""

from typing import Protocol

from civitas.contracts.execution import ExecutionRequest, ExecutionResult


class ExecutionPort(Protocol):
    async def execute(self, request: ExecutionRequest) -> ExecutionResult: ...
