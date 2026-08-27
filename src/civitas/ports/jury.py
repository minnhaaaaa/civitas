"""Jury evaluation interface."""

from typing import Protocol

from civitas.contracts.jury import JuryEvaluation, JuryRequest


class JuryPort(Protocol):
    async def evaluate(self, request: JuryRequest) -> JuryEvaluation: ...
