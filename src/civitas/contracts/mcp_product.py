"""Versioned, transport-neutral contracts for the Civitas MCP product surface.

These types are deliberately independent of MCP SDK, HTTP, persistence, and
provider concerns.  Inbound adapters authenticate a caller, resolve an
``OperatorContext``, and delegate to the product-service port with these
strict payloads.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from civitas.contracts.common import Contract, JsonObject
from civitas.contracts.enums import ExecutionState, JuryState

MCP_PRODUCT_CONTRACT_VERSION: Literal["1"] = "1"
APPROVAL_CONTRACT_VERSION: Literal["1"] = "1"
MAX_PAGE_SIZE = 50
MAX_GOAL_HORIZON = timedelta(days=31)

Identifier = str
"""Opaque, organization-scoped identifier with a conservative wire format."""


class ProductErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    EXPIRED_APPROVAL = "expired_approval"
    STALE_DATA = "stale_data"
    INVESTIGATION_REQUIRED = "investigation_required"
    ESCALATION_REQUIRED = "escalation_required"
    REJECTED_EXECUTION = "rejected_execution"
    DUPLICATE_EXECUTION = "duplicate_execution"


class PlanningRunStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    INVESTIGATING = "investigating"
    READY_FOR_APPROVAL = "ready_for_approval"
    ESCALATED = "escalated"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"


class ProductContract(Contract):
    """Base for every public request and response contract."""

    contract_version: Literal["1"] = MCP_PRODUCT_CONTRACT_VERSION


class ProductError(ProductContract):
    code: ProductErrorCode
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    occurred_at: datetime
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)


class ProductServiceError(Exception):
    """Typed application error that adapters can map without leaking internals."""

    def __init__(self, error: ProductError) -> None:
        self.error = error
        super().__init__(error.message)


class PageRequest(ProductContract):
    """Bounded opaque cursor pagination shared by progress and audit reads."""

    cursor: str | None = Field(default=None, min_length=4, max_length=512)
    page_size: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE)

    @field_validator("cursor")
    @classmethod
    def validate_cursor(cls, cursor: str | None) -> str | None:
        if cursor is None:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
            payload = json.loads(decoded)
        except (
            UnicodeEncodeError,
            binascii.Error,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            raise ValueError("cursor must be a URL-safe base64 JSON object") from error
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError("cursor must contain supported version v=1")
        after = payload.get("after")
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise ValueError("cursor must contain a non-negative integer after value")
        return cursor


class ProgressCursor(ProductContract):
    """Canonical cursor payload before URL-safe base64 encoding by the adapter."""

    v: Literal[1] = 1
    after: int = Field(ge=0)


class ProcurementGoal(ProductContract):
    """A bounded planning objective; free text alone is intentionally insufficient."""

    objective: str = Field(min_length=1, max_length=1_000)
    horizon_starts_at: datetime
    horizon_ends_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    sku_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    warehouse_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=100)
    maximum_cycles: int = Field(ge=1, le=10)
    model_call_budget: int = Field(ge=0, le=100)
    tool_call_budget: int = Field(ge=1, le=500)
    deadline_at: datetime
    constraints: JsonObject = Field(default_factory=dict, max_length=100)

    @field_validator("horizon_starts_at", "horizon_ends_at", "deadline_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone offset")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_bounds(self) -> ProcurementGoal:
        duration = self.horizon_ends_at - self.horizon_starts_at
        if duration <= timedelta(0):
            raise ValueError("horizon_ends_at must be after horizon_starts_at")
        if duration > MAX_GOAL_HORIZON:
            raise ValueError("planning horizon may not exceed 31 days")
        if self.deadline_at <= self.horizon_starts_at:
            raise ValueError("deadline_at must be after horizon_starts_at")
        return self


class PlanProcurementGoalRequest(ProductContract):
    goal: ProcurementGoal
    client_request_id: str | None = Field(default=None, min_length=1, max_length=128)


class PlanningProgress(ProductContract):
    sequence: int = Field(ge=0)
    occurred_at: datetime
    phase: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    reason_codes: tuple[str, ...] = Field(default=(), max_length=20)


class PlanningRun(ProductContract):
    organization_id: Identifier = Field(min_length=1, max_length=128)
    run_id: Identifier = Field(min_length=1, max_length=128)
    status: PlanningRunStatus
    policy_version: str = Field(min_length=1, max_length=64)
    created_at: datetime
    updated_at: datetime
    selected_plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    outstanding_investigation: tuple[str, ...] = Field(default=(), max_length=20)


class PlanningRunResponse(ProductContract):
    run: PlanningRun
    progress: tuple[PlanningProgress, ...] = Field(default=(), max_length=MAX_PAGE_SIZE)
    next_cursor: str | None = Field(default=None, min_length=4, max_length=512)


class GetPlanningRunRequest(PageRequest):
    run_id: Identifier = Field(min_length=1, max_length=128)


class BusinessImpact(ProductContract):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    total_landed_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    expected_waste_value: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    shortage_base_units: int = Field(ge=0)
    procurement_line_count: int = Field(ge=0, le=10_000)
    distribution_line_count: int = Field(ge=0, le=10_000)


class IntegritySummary(ProductContract):
    score: float = Field(ge=0, le=100)
    state: JuryState
    hard_gates_passed: bool
    reason_codes: tuple[str, ...] = Field(default=(), max_length=50)


class DecisionSummary(ProductContract):
    organization_id: Identifier = Field(min_length=1, max_length=128)
    run_id: Identifier = Field(min_length=1, max_length=128)
    status: PlanningRunStatus
    policy_version: str = Field(min_length=1, max_length=64)
    generated_at: datetime
    selected_plan_id: Identifier | None = Field(default=None, min_length=1, max_length=128)
    selected_plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    business_impact: BusinessImpact | None = None
    integrity: IntegritySummary | None = None
    material_uncertainties: tuple[str, ...] = Field(default=(), max_length=20)
    audit_link: str | None = Field(default=None, max_length=2_048)


class GetDecisionSummaryRequest(ProductContract):
    run_id: Identifier = Field(min_length=1, max_length=128)


class ApprovedTotals(ProductContract):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    maximum_landed_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=4)
    maximum_procurement_lines: int = Field(ge=0, le=10_000)
    maximum_distribution_lines: int = Field(ge=0, le=10_000)


class PrepareExecutionRequest(ProductContract):
    run_id: Identifier = Field(min_length=1, max_length=128)
    selected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ApprovalChallenge(ProductContract):
    approval_contract_version: Literal["1"] = APPROVAL_CONTRACT_VERSION
    challenge_id: Identifier = Field(min_length=1, max_length=128)
    challenge_secret: str = Field(min_length=16, max_length=512)
    organization_id: Identifier = Field(min_length=1, max_length=128)
    operator_id: Identifier = Field(min_length=1, max_length=128)
    run_id: Identifier = Field(min_length=1, max_length=128)
    selected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_version: str = Field(min_length=1, max_length=64)
    approved_totals: ApprovedTotals
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_expiry(self) -> ApprovalChallenge:
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("approval timestamps must include a timezone offset")
        if self.expires_at <= self.issued_at:
            raise ValueError("approval expiry must be after issuance")
        return self


class PrepareExecutionResponse(ProductContract):
    decision: DecisionSummary
    challenge: ApprovalChallenge


class ApproveExecutionRequest(ProductContract):
    challenge_id: Identifier = Field(min_length=1, max_length=128)
    challenge_secret: str = Field(min_length=16, max_length=512)


class ApprovalReceipt(ProductContract):
    approval_contract_version: Literal["1"] = APPROVAL_CONTRACT_VERSION
    receipt_id: Identifier = Field(min_length=1, max_length=128)
    organization_id: Identifier = Field(min_length=1, max_length=128)
    operator_id: Identifier = Field(min_length=1, max_length=128)
    run_id: Identifier = Field(min_length=1, max_length=128)
    selected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_version: str = Field(min_length=1, max_length=64)
    approved_totals: ApprovedTotals
    approved_at: datetime
    expires_at: datetime


class ApproveExecutionResponse(ProductContract):
    receipt: ApprovalReceipt


class ExecuteApprovedPlanRequest(ProductContract):
    receipt_id: Identifier = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._:-]+$")


class AuditLink(ProductContract):
    href: str = Field(min_length=1, max_length=2_048)
    expires_at: datetime | None = None


class ExecutionReceipt(ProductContract):
    receipt_id: Identifier = Field(min_length=1, max_length=128)
    organization_id: Identifier = Field(min_length=1, max_length=128)
    run_id: Identifier = Field(min_length=1, max_length=128)
    selected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=255)
    execution_state: ExecutionState
    duplicate: bool
    executed_at: datetime
    external_references: tuple[str, ...] = Field(default=(), max_length=100)
    compensation_state: ExecutionState | None = None
    audit_link: AuditLink | None = None


class ExecuteApprovedPlanResponse(ProductContract):
    execution: ExecutionReceipt


class GetExecutionAuditRequest(PageRequest):
    run_id: Identifier = Field(min_length=1, max_length=128)
    execution_receipt_id: Identifier = Field(min_length=1, max_length=128)


class ExecutionAuditEntry(ProductContract):
    sequence: int = Field(ge=0)
    occurred_at: datetime
    state: ExecutionState
    reason_code: str | None = Field(default=None, max_length=128)
    detail: str | None = Field(default=None, max_length=500)


class ExecutionAuditResponse(ProductContract):
    execution: ExecutionReceipt
    entries: tuple[ExecutionAuditEntry, ...] = Field(default=(), max_length=MAX_PAGE_SIZE)
    next_cursor: str | None = Field(default=None, min_length=4, max_length=512)


TOOL_REQUEST_CONTRACTS: dict[str, type[ProductContract]] = {
    "plan_procurement_goal": PlanProcurementGoalRequest,
    "get_planning_run": GetPlanningRunRequest,
    "get_decision_summary": GetDecisionSummaryRequest,
    "prepare_execution": PrepareExecutionRequest,
    "approve_execution": ApproveExecutionRequest,
    "execute_approved_plan": ExecuteApprovedPlanRequest,
    "get_execution_audit": GetExecutionAuditRequest,
}

TOOL_RESPONSE_CONTRACTS: dict[str, type[ProductContract]] = {
    "plan_procurement_goal": PlanningRunResponse,
    "get_planning_run": PlanningRunResponse,
    "get_decision_summary": DecisionSummary,
    "prepare_execution": PrepareExecutionResponse,
    "approve_execution": ApproveExecutionResponse,
    "execute_approved_plan": ExecuteApprovedPlanResponse,
    "get_execution_audit": ExecutionAuditResponse,
}


def product_json_schemas() -> dict[str, JsonObject]:
    """Return deterministic JSON schemas keyed by public tool and direction."""

    schemas: dict[str, JsonObject] = {}
    for name, contract_type in TOOL_REQUEST_CONTRACTS.items():
        schemas[f"{name}.request"] = contract_type.model_json_schema()
    for name, contract_type in TOOL_RESPONSE_CONTRACTS.items():
        schemas[f"{name}.response"] = contract_type.model_json_schema()
    return {name: schemas[name] for name in sorted(schemas)}
