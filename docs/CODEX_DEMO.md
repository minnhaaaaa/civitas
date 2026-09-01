# Civitas Codex judge demo

This is a production-shaped, two-to-three-minute local demonstration of the target
Codex/MCP workflow. It uses a mutable simulated operational provider, runs the real
optimizer, Parliament/Jury workflow, approval boundary, and execution ledger, and
must not make a live procurement-provider call.

## MCP-first connection flow

The installed Civitas MCP starts with no operational provider enabled. The first
`plan_procurement_goal` returns `connection_required` with the missing evidence
capabilities and three choices: the mutable Civitas sandbox, a live Remote MCP, or
Odoo. For the offline judge flow, choose the sandbox, call
`enable_sandbox_provider` for `evidence`, and resume the same run ID.

After the plan is prepared and explicitly approved, `approve_execution` pauses
again when no purchase provider exists. Choose the sandbox purchase provider by
enabling `execution`. A live Remote MCP or Odoo option instead returns an external
authorization URL; credentials are never accepted in the conversation. Only then
call `execute_approved_plan`.

The sandbox purchase is retained in the MCP process ledger. Reusing the same
idempotency key returns `duplicate: true` and preserves the original PO reference.
The sandbox never contacts a supplier or moves money. `update_sandbox_offer`
mutates a quote and increments its observation version for subsequent runs.

To prove supplier choice is data-driven, set Supplier A's sandbox lead time to one
day and Supplier B's to ten days before resuming a paused run. The real optimizer
then selects Supplier A (four units, USD 16) instead of the baseline Supplier B
(four units, USD 28), and the Jury evaluates the changed plan.

## Reset

Start from a clean local database and simulated provider state. The deployment profile supplied by the MCP server and worker workstreams owns the exact reset command. Do not reuse a previous run, approval receipt, or idempotency key.

For the current offline scenario fixtures, run:

```bash
uv run pytest tests/golden/test_scenarios.py -q
```

The checked-in plugin launches a self-contained, side-effect-safe STDIO demo composition:

```json
{
  "command": "uvx",
  "args": ["--from", "git+https://github.com/minnhaaaaa/civitas", "civitas-mcp-demo"]
}
```

The demo composition uses the real product facade, optimizer, Parliament workflow, evidence/Jury policy, approval binding, freshness revalidation, and idempotency protection. Its identity, persistence, and outbound provider are deterministic in-memory substitutes; it cannot create a real purchase order. Production deployments continue to use `civitas.mcp_server` with explicit PostgreSQL, identity, approval, and provider configuration.

```bash
uv run pytest tests/end_to_end/mcp/test_demo_service.py -q
```

## Judge script

1. In Codex, enable the repository-local `plugins/civitas` plugin and ask it to satisfy tomorrow's demand while minimizing cost and waste.
2. Confirm that `plan_procurement_goal` pauses with `connection_required`. Choose the evidence sandbox, then confirm Codex resumes the same run ID.
3. Let Codex poll `get_planning_run` until a material outcome. Observe investigation after stale shared supplier evidence, then replanning after clean-room Dissent retrieval.
4. Ask for the decision. Codex calls `get_decision_summary` and reports the selected solver-validated plan, business impact, Integrity result, hard-gate result, and any material uncertainty. It offers the audit link only if you ask to inspect deeper evidence.
5. Ask to execute. Codex calls `prepare_execution` with the exact selected plan hash and presents the short-lived challenge, bound totals, and expiry. Explicitly approve that exact challenge.
6. `approve_execution` returns a second connection requirement. Choose the sandbox purchase provider; only then may Codex call `execute_approved_plan` with a new idempotency key.
7. Simulate an uncertain client response and ask Codex to retry the same execution. It reuses the same receipt and idempotency key. Verify that Civitas returns the original execution receipt marked as a duplicate rather than creating a second procurement order.

## Pass conditions

- The workflow reaches the false-consensus investigation and replan path without conversationally bypassing Civitas.
- Codex does not claim a quantity, Jury score, approval, or execution outcome that is absent from a tool response.
- Execution follows `prepare_execution` → explicit operator approval → `approve_execution` → `execute_approved_plan`.
- The identical retry returns the original receipt with duplicate status.
- No credentials, user home directory, or developer-specific absolute path appears in the plugin configuration or demo transcript.
