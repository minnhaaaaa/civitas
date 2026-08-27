# Civitas MCP product interface

## Product decision

The primary Civitas user experience is conversational. Codex or another compatible agent acts as the interaction layer and invokes Civitas through an inbound MCP server. The React application is optional: it provides a deep, read-only evidence-lineage and execution-audit view when an operator needs more detail.

This removes the requirement to learn or continuously monitor a dashboard without moving procurement authority into the conversational model.

## Dual-sided MCP architecture

```text
Operator
   ↓ natural-language goal and explicit approval
Codex
   ↓ inbound intent-level MCP
Civitas MCP facade
   ↓ application services
Planner → OR-Tools → Parliament → Jury / Dissent → guarded execution
   ↓ outbound least-privilege MCP
Inventory, demand, warehouse, supplier, transport, and procurement systems
```

The two MCP boundaries have different trust levels:

- **Inbound MCP:** exposes bounded procurement workflows to Codex. It never exposes repositories, arbitrary SQL, unrestricted provider calls, or direct quantity mutation.
- **Outbound MCP:** lets Civitas retrieve operational evidence and perform approved writes. Dissent receives read-only tools; execution receives only the write tools required for the approved action.

## Initial tool surface

Keep the public tool catalog small and intent-level:

| Tool | Side effect | Purpose |
| --- | --- | --- |
| `plan_procurement_goal` | No | Validate a typed goal and start a bounded planning run. |
| `get_planning_run` | No | Return status, current phase, bounded progress, and outstanding investigation. |
| `get_decision_summary` | No | Return alternatives, selected plan, Jury score, hard gates, and concise evidence findings. |
| `prepare_execution` | No | Refresh mutable inputs and issue a short-lived approval challenge for an immutable plan hash. |
| `approve_execution` | Approval record only | Bind an authenticated operator to the exact plan hash, totals, policy version, and expiry. |
| `execute_approved_plan` | Yes | Revalidate and execute once using a required idempotency key. |
| `get_execution_audit` | No | Return the immutable outcome, duplicate status, and compensation state. |

Investigation and replanning remain internal workflow transitions. A diagnostic deployment may expose read-only evidence resources, but the normal agent should not orchestrate low-level Parliament or repository operations itself.

## Versioned wire contracts

All public tool requests and responses use MCP product contract version `1`. The
canonical Python definitions are `civitas.contracts.mcp_product`; MCP and HTTP
adapters must generate their schemas from those definitions rather than create
parallel DTOs. Unknown fields are rejected at every public boundary.

| Tool | Request contract | Response contract |
| --- | --- | --- |
| `plan_procurement_goal` | `PlanProcurementGoalRequest` | `PlanningRunResponse` |
| `get_planning_run` | `GetPlanningRunRequest` | `PlanningRunResponse` |
| `get_decision_summary` | `GetDecisionSummaryRequest` | `DecisionSummary` |
| `prepare_execution` | `PrepareExecutionRequest` | `PrepareExecutionResponse` |
| `approve_execution` | `ApproveExecutionRequest` | `ApproveExecutionResponse` |
| `execute_approved_plan` | `ExecuteApprovedPlanRequest` | `ExecuteApprovedPlanResponse` |
| `get_execution_audit` | `GetExecutionAuditRequest` | `ExecutionAuditResponse` |

`plan_procurement_goal` requires a bounded `ProcurementGoal`: explicit
timezone-aware horizon start and end timestamps (at most 31 days), one or more
SKU and warehouse identifiers, maximum cycles, model and tool-call budgets,
and a deadline. An unbounded natural-language goal is invalid input.

Every planning and decision response carries the organization-scoped run ID,
current status, policy version, and timestamps needed by the next call. A
decision summary includes the selected immutable plan hash when a plan exists,
deterministic business impact, Integrity state, hard-gate outcome, material
uncertainties, and only an optional audit-view link.

### Cursor and payload limits

Progress and execution-audit reads use an opaque URL-safe base64 JSON cursor
with the canonical decoded shape `{"v": 1, "after": <non-negative integer>}`.
Malformed or unsupported cursors are `invalid_input`; clients must treat them
as opaque. Page size defaults to 20 and is limited to 50 records. Tool payloads
are concise summaries: large evidence graphs remain paginated resources or the
optional audit viewer, never unbounded tool responses.

### Approval contract

Approval contract version `1` binds the following values into a short-lived
challenge and then the resulting approval receipt:

```text
organization ID + authenticated operator ID + planning run ID
+ selected immutable plan hash + policy version + approved totals
+ issued time + expiry
```

`prepare_execution` returns an approval challenge, including its one-time
secret. The persistence implementation stores only a hash of that secret.
`approve_execution` requires both challenge ID and secret; it returns the
receipt used by `execute_approved_plan`. The execute request additionally
requires an idempotency key. Any changed plan hash, expired or consumed
challenge, stale refresh, failed Jury gate, or material revalidation change
prevents execution.

### Stable error codes

Adapters map typed application errors to these non-sensitive codes without
returning stack traces, SQL errors, credentials, or cross-organization IDs:

```text
invalid_input | not_found | conflict | expired_approval | stale_data
investigation_required | escalation_required | rejected_execution
duplicate_execution
```

The application service receives an authenticated `OperatorContext` containing
organization ID, operator ID, authentication subject, authentication time,
roles, and correlation ID. This context is derived by the transport and is not
accepted from a tool payload.

## Conversational contract

Codex may summarize and explain results, but structured MCP responses remain authoritative. Every response includes `organization_id`, `run_id`, `status`, policy version, timestamps, and stable identifiers required for the next operation.

The recommended conversation is:

1. The operator states a procurement objective.
2. Codex starts the run and reports meaningful transitions rather than every internal event.
3. Civitas retrieves evidence, generates solver alternatives, runs Parliament and Jury, investigates, and replans as required.
4. Codex presents the selected plan, business impact, Integrity components, hard gates, and material uncertainties.
5. Civitas prepares an immutable execution challenge.
6. The operator explicitly approves that exact challenge.
7. Civitas performs final freshness and feasibility checks and executes with duplicate protection.
8. Codex returns the execution receipt and an optional audit-view link.

## Approval and execution invariants

A conversational statement such as “yes” is intent, not execution authority by itself. Execution requires all of the following:

```text
authenticated organization and operator
exact immutable plan hash
approved totals and policy version
short-lived approval challenge
freshness and feasibility revalidation
all applicable Jury hard gates
required idempotency key
transactional local reservations
immutable execution audit record
```

If the plan content, price ceiling, capacity, supplier availability, or other material fact changes, the challenge becomes invalid and the workflow returns to investigation or escalation. Retrying the same execution returns the original result.

## Transport and deployment

- Use STDIO for local development and a one-machine judge demonstration.
- Use Streamable HTTP for deployed integrations.
- Require OAuth for user-facing multi-tenant deployments; a rotated bearer token is acceptable only for controlled single-tenant environments.
- Apply organization scope at every tool call and persisted lookup.
- Keep the offline demo server separate from the guarded remote MCP deployment.
- Return bounded structured payloads. Large evidence graphs belong in paginated resources or the optional audit viewer.

## Optional audit viewer

The existing React application remains useful for:

- evidence-lineage graph inspection;
- shared-source and echo visualization;
- Integrity component and hard-gate review;
- investigation and replanning timelines; and
- execution receipts and compensation diagnostics.

It must not create an alternative execution path. Any approval initiated from the viewer must call the same guarded application service and satisfy the same immutable challenge contract as MCP.

## Delivery status

The current repository implements the core workflow, demonstration API, guarded API services, SSE events, and optional viewer. It does not yet expose the inbound MCP tool surface above. Until that facade, authentication composition, and durable production worker are implemented, documentation and demos must describe Codex/MCP as the approved target interface rather than a completed deployment feature.
