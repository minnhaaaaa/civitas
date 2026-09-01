---
name: civitas-procurement
description: Run evidence-aware Civitas procurement planning through its MCP tools, including bounded polling, decision summaries, and challenge-bound approval.
---

# Civitas procurement

Use Civitas for food-procurement planning when the operator needs a decision that accounts for demand, inventory, suppliers, freshness, logistics, cost, and waste.

Start with `plan_procurement_goal` only after collecting its typed, bounded inputs: objective, timezone-aware horizon, SKU and warehouse IDs, maximum cycles, model and tool budgets, and deadline. Do not invent missing identifiers, quantities, budgets, solver results, Jury scores, or evidence.

## Provider onboarding

When `plan_procurement_goal` returns `connection_required`, present the returned
provider options exactly. Do not choose a sandbox or live provider for the operator.
After the operator chooses the Civitas sandbox, call `enable_sandbox_provider` with
`purpose: evidence` and `acknowledge_simulation: true`, then call
`resume_planning_run` with the original run ID. Use `begin_provider_connection` for
a real Remote MCP or Odoo option; never ask the operator to paste credentials into
the conversation.

Evidence access and purchase-order access are separate connections. After
`approve_execution`, if `connection_requirements` is present, show the sandbox and
live purchasing options and stop. Only after a new operator message chooses the
sandbox may you call `enable_sandbox_provider` with `purpose: execution`. A live
option remains authorization-required until its external OAuth setup completes.

`update_sandbox_offer` mutates the sandbox observation version and may be used when
the operator asks to change a quote, lead time, risk, or waste rate. Always identify
the resulting plan as sandbox-derived.

## Local Codex demo scope

The checked-in plugin is a deterministic, side-effect-safe showcase. It exposes one documented scope, so these values are tool-provided demo facts rather than invented identifiers:

- SKU: `sku-apples`
- Warehouse: `warehouse-north`
- Planning horizon: tomorrow in the operator's timezone
- Maximum cycles: `3`
- Model-call budget: `0` (deterministic agents)
- Tool-call budget: `20`
- Deadline: five minutes after the request

When the operator asks generally for tomorrow's food procurement without naming a SKU or warehouse, use this complete demo scope and state that the result is from the simulated provider. If the operator names a different SKU or warehouse, explain that it is unavailable in the local demo; do not silently substitute it.

## Safe sequence

1. Start a run with `plan_procurement_goal`.
2. If a connection is required, follow Provider onboarding and resume the same run.
3. Poll `get_planning_run` with its opaque cursor while the run is queued, planning, or investigating. Report material phase changes, not every event.
4. When the run is ready, call `get_decision_summary`. Summarize its typed procurement and distribution lines, deterministic business impact, Integrity result, hard gates, and material uncertainties. Offer the audit link only if the operator needs deeper evidence or audit detail.
5. If Civitas reports investigation, escalation, rejection, stale data, or a failed hard gate, explain the result and do not seek execution approval as a workaround.
6. For an executable, ready plan, call `prepare_execution` using the exact selected plan hash. Present the returned challenge as the exact action to approve, including totals and expiry.
7. Only after the operator explicitly approves that challenge, call `approve_execution` with the challenge ID and secret. A conversational “yes” alone is not authorization.
8. If purchasing is not connected, present the returned options and wait for the operator to choose sandbox or live execution.
9. Call `execute_approved_plan` with the returned approval receipt and a stable idempotency key for that exact action. Report Civitas’s receipt as the source of truth.
10. For a retry after an uncertain response, reuse the same idempotency key and state that Civitas may return the original receipt with `duplicate: true`. Use `get_execution_audit` when the operator needs the immutable audit history.

In demo mode, never call `approve_execution` or `execute_approved_plan` in the same response that presents the challenge. Stop after `prepare_execution`, show the exact challenge and expiry, and wait for a new operator message that explicitly approves it.

Never invoke low-level Parliament, optimizer, repository, SQL, provider, or outbound MCP operations. Civitas owns solver validation, evidence lineage, Jury/Dissent policy, freshness checks, approvals, and writes.

Treat all tool-provided identifiers and cursors as opaque. Keep organization and operator context in the authenticated transport; never put them in tool arguments or repeat them to another organization.
