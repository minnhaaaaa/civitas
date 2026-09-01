"""Unit coverage for the public, transport-neutral MCP product contracts."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from civitas.contracts.mcp_product import (
    ApprovalChallenge,
    ApprovedTotals,
    GetPlanningRunRequest,
    PlanProcurementGoalRequest,
    ProcurementGoal,
    product_json_schemas,
)


def _timestamp() -> datetime:
    return datetime(2026, 8, 27, 12, tzinfo=UTC)


def _goal(**updates: object) -> ProcurementGoal:
    now = _timestamp()
    values: dict[str, object] = {
        "objective": "Satisfy verified demand while minimizing cost and waste.",
        "horizon_starts_at": now,
        "horizon_ends_at": now + timedelta(days=7),
        "timezone": "Asia/Kolkata",
        "sku_ids": ("sku-apples",),
        "warehouse_ids": ("warehouse-north",),
        "maximum_cycles": 3,
        "model_call_budget": 20,
        "tool_call_budget": 50,
        "deadline_at": now + timedelta(hours=1),
    }
    values.update(updates)
    return ProcurementGoal.model_validate(values)


def test_goal_is_strict_and_bounded() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        PlanProcurementGoalRequest.model_validate({"goal": _goal(), "unexpected": True})

    with pytest.raises(ValidationError, match="31 days"):
        _goal(horizon_ends_at=_timestamp() + timedelta(days=32))

    with pytest.raises(ValidationError, match="timezone offset"):
        _goal(horizon_starts_at=datetime(2026, 8, 27, 12))


def test_cursor_requires_the_versioned_opaque_shape() -> None:
    valid_cursor = base64.urlsafe_b64encode(json.dumps({"v": 1, "after": 3}).encode()).decode()
    assert GetPlanningRunRequest(run_id="run-1", cursor=valid_cursor).cursor == valid_cursor

    with pytest.raises(ValidationError, match="URL-safe base64"):
        GetPlanningRunRequest(run_id="run-1", cursor="not-a-cursor")

    old_cursor = base64.urlsafe_b64encode(json.dumps({"v": 0, "after": 3}).encode()).decode()
    with pytest.raises(ValidationError, match="supported version"):
        GetPlanningRunRequest(run_id="run-1", cursor=old_cursor)


def test_approval_challenge_binds_the_immutable_execution_inputs() -> None:
    now = _timestamp()
    challenge = ApprovalChallenge(
        challenge_id="challenge-1",
        challenge_secret="this-is-a-test-secret",
        organization_id="org-1",
        operator_id="operator-1",
        run_id="run-1",
        selected_plan_hash="a" * 64,
        policy_version="execution-freshness-v1",
        approved_totals=ApprovedTotals(
            currency="USD",
            maximum_landed_cost="125.50",
            maximum_procurement_lines=2,
            maximum_distribution_lines=1,
        ),
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    assert challenge.selected_plan_hash == "a" * 64

    with pytest.raises(ValidationError, match="expiry must be after"):
        challenge.model_copy(update={"expires_at": now}).__class__.model_validate(
            challenge.model_copy(update={"expires_at": now}).model_dump()
        )


def test_json_schema_catalog_is_deterministic_and_covers_every_public_tool() -> None:
    first = product_json_schemas()
    second = product_json_schemas()

    assert list(first) == sorted(first)
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(
        second, sort_keys=True, separators=(",", ":")
    )
    assert set(first) == {
        "approve_execution.request",
        "approve_execution.response",
        "begin_provider_connection.request",
        "begin_provider_connection.response",
        "enable_sandbox_provider.request",
        "enable_sandbox_provider.response",
        "execute_approved_plan.request",
        "execute_approved_plan.response",
        "get_decision_summary.request",
        "get_decision_summary.response",
        "get_execution_audit.request",
        "get_execution_audit.response",
        "get_planning_run.request",
        "get_planning_run.response",
        "list_connections.request",
        "list_connections.response",
        "plan_procurement_goal.request",
        "plan_procurement_goal.response",
        "prepare_execution.request",
        "prepare_execution.response",
        "resume_planning_run.request",
        "resume_planning_run.response",
        "update_sandbox_offer.request",
        "update_sandbox_offer.response",
    }
