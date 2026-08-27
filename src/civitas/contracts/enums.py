"""Enums used across application boundaries."""

from enum import StrEnum


class EvidenceOrigin(StrEnum):
    EXTERNAL = "external"
    AGENT_DERIVED = "agent_derived"


class FeasibilityStatus(StrEnum):
    FULLY_FEASIBLE = "fully_feasible"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    INFEASIBLE = "infeasible"


class JuryState(StrEnum):
    APPROVE = "approve"
    INVESTIGATE = "investigate"
    ESCALATE = "escalate"
    REJECT = "reject"


class ExecutionState(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    COMPENSATION_REQUIRED = "compensation_required"
    COMPENSATED = "compensated"
    DUPLICATE = "duplicate"


class WorkflowEventType(StrEnum):
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    EVIDENCE_RECORDED = "evidence.recorded"
    PROPOSAL_CREATED = "proposal.created"
    JURY_EVALUATED = "jury.evaluated"
    INVESTIGATION_REQUESTED = "investigation.requested"
    EXECUTION_UPDATED = "execution.updated"
