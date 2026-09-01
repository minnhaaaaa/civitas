"""Complete Codex-plugin demo sequence through the real MCP product boundary."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from civitas.mcp_server.demo import build_demo_server


def _goal() -> dict[str, object]:
    now = datetime.now(UTC)
    starts = now + timedelta(minutes=1)
    return {
        "objective": "Satisfy tomorrow's food demand while minimizing cost and waste.",
        "horizon_starts_at": starts.isoformat(),
        "horizon_ends_at": (starts + timedelta(days=1)).isoformat(),
        "timezone": "UTC",
        "sku_ids": ["sku-apples"],
        "warehouse_ids": ["warehouse-north"],
        "maximum_cycles": 3,
        "model_call_budget": 0,
        "tool_call_budget": 20,
        "deadline_at": (now + timedelta(minutes=5)).isoformat(),
    }


@pytest.mark.asyncio
async def test_demo_mcp_runs_replans_challenges_and_duplicate_protects() -> None:
    server = build_demo_server()
    goal = _goal()

    planned = await server.dispatch(
        "plan_procurement_goal",
        {"goal": goal, "client_request_id": "codex-demo-1"},
    )
    assert planned["run"]["status"] == "connection_required"
    assert planned["connection_requirements"]["missing_capabilities"]
    run_id = planned["run"]["run_id"]
    evidence_connection = await server.dispatch(
        "enable_sandbox_provider",
        {"purpose": "evidence", "acknowledge_simulation": True},
    )
    assert evidence_connection["connection"]["state"] == "connected"
    planned = await server.dispatch("resume_planning_run", {"run_id": run_id})
    assert planned["run"]["status"] == "ready_for_approval"
    messages = [item["message"] for item in planned["progress"]]
    assert any("Transport capacity" in message for message in messages)

    polled = await server.dispatch("get_planning_run", {"run_id": run_id})
    assert polled["run"]["status"] == "ready_for_approval"

    summary = await server.dispatch("get_decision_summary", {"run_id": run_id})
    assert summary["selected_plan_id"] is not None
    assert summary["selected_plan_hash"] is not None
    assert summary["business_impact"]["shortage_base_units"] == 0
    assert summary["procurement_lines"] == [
        {
            "supplier_id": "supplier-b",
            "sku_id": "sku-apples",
            "destination_warehouse_id": "warehouse-north",
            "arrival_bucket_start": goal["horizon_starts_at"].replace("+00:00", "Z"),
            "quantity": {"value": "4", "unit": "each"},
            "landed_cost": "28",
        }
    ]
    assert summary["integrity"]["state"] == "approve"
    assert summary["integrity"]["hard_gates_passed"] is True

    prepared = await server.dispatch(
        "prepare_execution",
        {"run_id": run_id, "selected_plan_hash": summary["selected_plan_hash"]},
    )
    challenge = prepared["challenge"]
    assert challenge["run_id"] == run_id
    assert challenge["selected_plan_hash"] == summary["selected_plan_hash"]

    approved = await server.dispatch(
        "approve_execution",
        {
            "challenge_id": challenge["challenge_id"],
            "challenge_secret": challenge["challenge_secret"],
        },
    )
    assert approved["connection_requirements"]["missing_capabilities"] == [
        "create_procurement_order"
    ]
    execution_connection = await server.dispatch(
        "enable_sandbox_provider",
        {"purpose": "execution", "acknowledge_simulation": True},
    )
    assert execution_connection["connection"]["write_enabled"] is True
    receipt_id = approved["receipt"]["receipt_id"]
    execution_request = {"receipt_id": receipt_id, "idempotency_key": "codex-demo-order-1"}
    first = await server.dispatch("execute_approved_plan", execution_request)
    retry = await server.dispatch("execute_approved_plan", execution_request)
    assert first["execution"]["execution_state"] == "succeeded"
    assert first["execution"]["duplicate"] is False
    assert first["execution"]["external_references"]
    assert retry["execution"]["execution_state"] == "duplicate"
    assert retry["execution"]["duplicate"] is True

    audit = await server.dispatch(
        "get_execution_audit",
        {
            "run_id": run_id,
            "execution_receipt_id": first["execution"]["receipt_id"],
        },
    )
    assert [entry["state"] for entry in audit["entries"]] == ["succeeded", "duplicate"]


@pytest.mark.asyncio
async def test_demo_mcp_rejects_undocumented_nlp_scope() -> None:
    server = build_demo_server()
    goal = _goal()
    goal["sku_ids"] = ["sku-invented"]

    response = await server.dispatch("plan_procurement_goal", {"goal": goal})

    assert response["code"] == "invalid_input"
    assert "demo scope" in response["message"]


@pytest.mark.asyncio
async def test_mutable_sandbox_offer_changes_the_next_solver_plan() -> None:
    server = build_demo_server()
    planned = await server.dispatch("plan_procurement_goal", {"goal": _goal()})
    run_id = planned["run"]["run_id"]
    await server.dispatch(
        "enable_sandbox_provider",
        {"purpose": "evidence", "acknowledge_simulation": True},
    )
    updated_a = await server.dispatch(
        "update_sandbox_offer",
        {"supplier_id": "supplier-a", "lead_time_days": 1},
    )
    updated_b = await server.dispatch(
        "update_sandbox_offer",
        {"supplier_id": "supplier-b", "lead_time_days": 10},
    )
    assert updated_a["observation_version"] == "sandbox-v2"
    assert updated_b["observation_version"] == "sandbox-v3"
    await server.dispatch("resume_planning_run", {"run_id": run_id})
    summary = await server.dispatch("get_decision_summary", {"run_id": run_id})
    assert summary["status"] == "ready_for_approval"
    assert summary["procurement_lines"][0]["supplier_id"] == "supplier-a"
    assert summary["business_impact"]["total_landed_cost"] == "16"


@pytest.mark.asyncio
async def test_demo_negotiates_real_mcp_session_and_calls_a_tool() -> None:
    server = build_demo_server()
    async with create_connected_server_and_client_session(server.mcp) as session:
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "list_connections",
            "begin_provider_connection",
            "enable_sandbox_provider",
            "update_sandbox_offer",
            "resume_planning_run",
            "plan_procurement_goal",
            "get_planning_run",
            "get_decision_summary",
            "prepare_execution",
            "approve_execution",
            "execute_approved_plan",
            "get_execution_audit",
        }
        response = await session.call_tool(
            "plan_procurement_goal",
            arguments={"goal": _goal(), "client_request_id": "mcp-session-demo-1"},
        )
        payload = json.loads(response.content[0].text)
        assert payload["run"]["status"] == "connection_required"
