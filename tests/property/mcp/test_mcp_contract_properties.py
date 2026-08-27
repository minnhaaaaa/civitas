"""Property coverage for deterministic MCP safety invariants."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from civitas.contracts.mcp_product import (
    ApprovalChallenge,
    ApprovedTotals,
    GetPlanningRunRequest,
)
from civitas.contracts.tools import MCPToolCall


@given(st.integers(min_value=0, max_value=10_000))
def test_progress_cursor_round_trips_any_non_negative_sequence(after: int) -> None:
    cursor = base64.urlsafe_b64encode(json.dumps({"v": 1, "after": after}).encode()).decode()
    request = GetPlanningRunRequest(run_id="run-1", cursor=cursor)
    decoded = json.loads(base64.urlsafe_b64decode(request.cursor or ""))
    assert decoded == {"v": 1, "after": after}


@given(st.text(min_size=1, max_size=300))
def test_tool_names_are_never_interpreted_as_free_form_commands(tool_name: str) -> None:
    payload = {
        "call_id": "call-1",
        "server_name": "provider-1",
        "tool_name": tool_name,
        "arguments": {},
        "access_mode": "read",
    }
    permitted = (
        tool_name[0] in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        and all(
            character in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in tool_name
        )
    )
    if permitted:
        assert MCPToolCall.model_validate(payload).tool_name == tool_name
    else:
        with pytest.raises(ValidationError):
            MCPToolCall.model_validate(payload)


@given(st.integers(min_value=1, max_value=60))
def test_approval_challenge_expiry_is_an_immutable_time_ordering(minutes: int) -> None:
    issued_at = datetime(2026, 8, 27, tzinfo=UTC)
    challenge = ApprovalChallenge(
        challenge_id="challenge-1",
        challenge_secret="test-secret-is-long-enough",
        organization_id="org-1",
        operator_id="operator-1",
        run_id="run-1",
        selected_plan_hash="a" * 64,
        policy_version="execution-freshness-v1",
        approved_totals=ApprovedTotals(
            currency="USD",
            maximum_landed_cost="12.50",
            maximum_procurement_lines=2,
            maximum_distribution_lines=1,
        ),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=minutes),
    )
    assert challenge.expires_at > challenge.issued_at
