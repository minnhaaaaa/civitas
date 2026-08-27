# Civitas Codex judge demo

This is a deterministic, two-to-three-minute local demonstration of the target Codex/MCP workflow. It uses the simulated procurement provider and must not make a live model or procurement-provider call.

## Reset

Start from a clean local database and simulated provider state. The deployment profile supplied by the MCP server and worker workstreams owns the exact reset command. Do not reuse a previous run, approval receipt, or idempotency key.

For the current offline scenario fixtures, run:

```bash
uv run pytest tests/golden/test_scenarios.py -q
```

The checked-in plugin configuration shows the intended STDIO command:

```json
{
  "command": "uv",
  "args": ["run", "python", "-m", "civitas.mcp_server"],
  "env": {"CIVITAS_MCP_TRANSPORT": "stdio"}
}
```

`civitas.mcp_server` deliberately fails closed until a deployment composition supplies a real `ProductService` and authenticated operator context. Do not add credentials to this file. Use the deterministic transport-to-facade test below to verify the complete local tool sequence; provision the production composition before attempting this script in Codex.

```bash
uv run pytest tests/end_to_end/mcp/test_product_sequence.py -q
```

## Judge script

1. In Codex, enable the repository-local `plugins/civitas` plugin and say: “Plan seven days of demand across the supplied warehouses while minimizing waste.”
2. Confirm that Codex calls `plan_procurement_goal` with bounded typed inputs. It must not fabricate SKU IDs, warehouse IDs, quantities, or a decision.
3. Let Codex poll `get_planning_run` until a material outcome. In the false-consensus fixture, observe investigation after stale shared supplier evidence, then replanning after clean-room Dissent retrieval.
4. Ask for the decision. Codex calls `get_decision_summary` and reports the selected solver-validated plan, business impact, Integrity result, hard-gate result, and any material uncertainty. It offers the audit link only if you ask to inspect deeper evidence.
5. Ask to execute. Codex calls `prepare_execution` with the exact selected plan hash and presents the short-lived challenge, bound totals, and expiry. Explicitly approve that exact challenge.
6. Codex calls `approve_execution`, then `execute_approved_plan` with a new idempotency key. Verify that the returned execution receipt is authoritative.
7. Simulate an uncertain client response and ask Codex to retry the same execution. It reuses the same receipt and idempotency key. Verify that Civitas returns the original execution receipt marked as a duplicate rather than creating a second procurement order.

## Pass conditions

- The workflow reaches the false-consensus investigation and replan path without conversationally bypassing Civitas.
- Codex does not claim a quantity, Jury score, approval, or execution outcome that is absent from a tool response.
- Execution follows `prepare_execution` → explicit operator approval → `approve_execution` → `execute_approved_plan`.
- The identical retry returns the original receipt with duplicate status.
- No credentials, user home directory, or developer-specific absolute path appears in the plugin configuration or demo transcript.
