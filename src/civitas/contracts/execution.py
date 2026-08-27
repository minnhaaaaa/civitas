"""Guarded execution contracts."""

from datetime import datetime

from civitas.contracts.common import Contract, JsonObject
from civitas.contracts.enums import ExecutionState


class ExecutionRequest(Contract):
    execution_id: str
    planning_run_id: str
    approved_plan_id: str
    jury_evaluation_id: str
    idempotency_key: str
    approval_policy_version: str
    requested_at: datetime
    action: JsonObject


class ExecutionResult(Contract):
    execution_id: str
    state: ExecutionState
    attempted_at: datetime
    completed_at: datetime | None = None
    external_references: tuple[str, ...] = ()
    failure_code: str | None = None
    detail: str | None = None
