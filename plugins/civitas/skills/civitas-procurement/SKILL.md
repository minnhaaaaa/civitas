---
name: civitas-procurement
description: Run evidence-aware Civitas procurement planning through its MCP tools, including bounded polling, decision summaries, and challenge-bound approval.
---

# Civitas procurement

Use Civitas for food-procurement planning when the operator needs a decision that accounts for demand, inventory, suppliers, freshness, logistics, cost, and waste.

Start with `plan_procurement_goal` only after collecting its typed, bounded inputs: objective, timezone-aware horizon, SKU and warehouse IDs, maximum cycles, model and tool budgets, and deadline. Do not invent missing identifiers, quantities, budgets, solver results, Jury scores, or evidence.

## Safe sequence

1. Start a run with `plan_procurement_goal`.
2. Poll `get_planning_run` with its opaque cursor while the run is queued, planning, or investigating. Report material phase changes, not every event.
3. When the run is ready, call `get_decision_summary`. Summarize the selected plan, deterministic business impact, Integrity result, hard gates, and material uncertainties. Offer the audit link only if the operator needs deeper evidence or audit detail.
4. If Civitas reports investigation, escalation, rejection, stale data, or a failed hard gate, explain the result and do not seek execution approval as a workaround.
5. For an executable, ready plan, call `prepare_execution` using the exact selected plan hash. Present the returned challenge as the exact action to approve, including totals and expiry.
6. Only after the operator explicitly approves that challenge, call `approve_execution` with the challenge ID and secret. A conversational “yes” alone is not authorization.
7. Call `execute_approved_plan` with the returned approval receipt and a stable idempotency key for that exact action. Report Civitas’s receipt as the source of truth.
8. For a retry after an uncertain response, reuse the same idempotency key and state that Civitas may return the original receipt with `duplicate: true`. Use `get_execution_audit` when the operator needs the immutable audit history.

Never invoke low-level Parliament, optimizer, repository, SQL, provider, or outbound MCP operations. Civitas owns solver validation, evidence lineage, Jury/Dissent policy, freshness checks, approvals, and writes.

Treat all tool-provided identifiers and cursors as opaque. Keep organization and operator context in the authenticated transport; never put them in tool arguments or repeat them to another organization.
