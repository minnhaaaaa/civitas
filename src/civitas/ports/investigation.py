"""Application port for bounded, durable investigation and replanning."""

from typing import Protocol

from civitas.workflow.models import InvestigationOutcome, WorkflowCheckpoint, WorkflowLimits


class PlanningInvestigator(Protocol):
    async def investigate(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        limits: WorkflowLimits,
    ) -> InvestigationOutcome: ...
