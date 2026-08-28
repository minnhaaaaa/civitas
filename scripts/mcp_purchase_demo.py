"""Black-box Codex-compatible MCP purchase-order demo client."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--token-env", default="CIVITAS_BEARER_TOKEN")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def _payload(result: Any) -> dict[str, Any]:
    if result.isError or not result.content:
        raise RuntimeError(f"MCP tool failed: {result}")
    text = getattr(result.content[0], "text", None)
    if not isinstance(text, str):
        raise RuntimeError("MCP tool did not return JSON text")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise RuntimeError("MCP tool returned a non-object response")
    return value


async def _run(args: argparse.Namespace) -> int:
    token = os.environ.get(args.token_env)
    if not token:
        raise RuntimeError(f"{args.token_env} is required")
    now = datetime.now(UTC)
    horizon_start = now + timedelta(hours=1)
    goal = {
        "objective": "Satisfy current demo demand while minimizing landed cost and waste",
        "horizon_starts_at": horizon_start.isoformat(),
        "horizon_ends_at": (horizon_start + timedelta(days=1)).isoformat(),
        "timezone": "UTC",
        "sku_ids": [os.environ.get("CIVITAS_SIMULATOR_SKU_ID", "sku-local")],
        "warehouse_ids": [
            os.environ.get("CIVITAS_SIMULATOR_WAREHOUSE_ID", "warehouse-local")
        ],
        "maximum_cycles": 3,
        "model_call_budget": 12,
        "tool_call_budget": 30,
        "deadline_at": (now + timedelta(hours=2)).isoformat(),
        "constraints": {},
    }
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(args.url, headers=headers, timeout=args.timeout) as streams:
        read, write, _ = streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            created = _payload(
                await session.call_tool("plan_procurement_goal", {"goal": goal})
            )
            run_id = created["run"]["run_id"]
            for _ in range(120):
                current = _payload(
                    await session.call_tool(
                        "get_planning_run", {"run_id": run_id, "page_size": 50}
                    )
                )
                status = current["run"]["status"]
                if status != "planning":
                    break
                await asyncio.sleep(0.25)
            else:
                raise TimeoutError("planning worker did not finish")
            summary = _payload(
                await session.call_tool("get_decision_summary", {"run_id": run_id})
            )
            print(json.dumps({"planning": summary}, indent=2))
            if status != "ready_for_approval":
                raise RuntimeError(f"planning did not reach approval: {status}")
            challenge = _payload(
                await session.call_tool(
                    "prepare_execution",
                    {
                        "run_id": run_id,
                        "selected_plan_hash": summary["selected_plan_hash"],
                    },
                )
            )["challenge"]
            if not args.auto_approve:
                print(
                    "Approval challenge created. Re-run with --auto-approve only for the "
                    "local simulator; production approval must come from the bound operator."
                )
                return 2
            approval = _payload(
                await session.call_tool(
                    "approve_execution",
                    {
                        "challenge_id": challenge["challenge_id"],
                        "challenge_secret": challenge["challenge_secret"],
                    },
                )
            )["receipt"]
            key = f"demo-po:{run_id}"
            execution = _payload(
                await session.call_tool(
                    "execute_approved_plan",
                    {"receipt_id": approval["receipt_id"], "idempotency_key": key},
                )
            )["execution"]
            duplicate = _payload(
                await session.call_tool(
                    "execute_approved_plan",
                    {"receipt_id": approval["receipt_id"], "idempotency_key": key},
                )
            )["execution"]
            print(json.dumps({"execution": execution, "duplicate_retry": duplicate}, indent=2))
            if execution["execution_state"] != "succeeded" or not duplicate["duplicate"]:
                raise RuntimeError("execution or duplicate protection failed")
            return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(_run(_arguments())))
    except Exception as error:
        print(f"demo failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
