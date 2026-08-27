"""Feasibility-first optimization interface."""

from typing import Protocol

from civitas.contracts.optimization import OptimizationRequest, OptimizationResult


class Optimizer(Protocol):
    async def solve(self, request: OptimizationRequest) -> OptimizationResult: ...
