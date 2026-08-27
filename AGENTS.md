# AGENTS.md

## Project

**Civitas — Autonomous Food Procurement through Agent Parliament and Agent Jury**

Civitas is a multi-agent autonomous procurement system based on Problem Statement 5 of the AgentXcelerate challenge.

The official problem asks the system to determine what food to procure, how much to procure, from which suppliers, and how inventory should be distributed across locations while considering demand, inventory, supplier lead times, pricing, perishability, and location requirements.

Civitas goes beyond this baseline by introducing:

1. **Agent Parliament** — autonomous agents with conflicting objectives negotiate a procurement plan.
2. **Agent Jury** — evaluates whether the reasoning and evidence behind the agreement deserve to be trusted.
3. **Adaptive Replanning** — a weak Jury result sends the system back into investigation and negotiation instead of allowing immediate execution.

---

# 1. Core Product Principle

> **Don't just make agents agree. Make sure their agreement deserves to be trusted.**

The system must distinguish:

```text
Agent Agreement
        ≠
Independent Evidence
        ≠
Correct Decision
```

A proposal supported by six agents can still be based on one underlying source or one propagated assumption.

Civitas therefore treats **evidence lineage** as a first-class object.

---

# 2. Architecture

The primary control loop is:

```text
USER GOAL
    ↓
PLANNER
    ↓
PARLIAMENT
    ↓
INVESTIGATION THROUGH MCP
    ↓
COMPETING PROPOSALS
    ↓
NEGOTIATION
    ↓
CANDIDATE PLAN
    ↓
JURY
    ↓
┌───────────────┐
│               │
▼               ▼
APPROVE      INVESTIGATE
│               │
▼               ▼
EXECUTE       REPLAN
                │
                └──────→ PARLIAMENT
```

The Jury is therefore part of the autonomous planning loop.

It is **not** a final dashboard or post-hoc evaluator.

## 2.1 Approved Architecture Amendments

The following rules are authoritative wherever an older example in this document is ambiguous.

### Solver-owned plan construction

OR-Tools owns procurement feasibility and allocation construction. Parliament agents may produce objectives, constraints, evidence-backed preferences, challenges, and suggested trade-offs. The system converts those positions into solver inputs and asks OR-Tools to generate one or more feasible alternatives.

An LLM-generated quantity is never executable merely because Parliament accepted it. Every candidate plan must be solver-produced or solver-validated before Jury review.

```text
Agent positions
      ↓
Typed objectives + constraints
      ↓
OR-Tools candidate alternatives
      ↓
Parliament comparison and negotiation
      ↓
Solver-validated candidate plan
      ↓
Jury
```

### Durable evidence ownership

PostgreSQL is the canonical system of record for evidence, claims, sources, tool calls, lineage edges, proposals, decisions, workflow events, and execution audit records. NetworkX is a disposable projection rebuilt from PostgreSQL for graph traversal and independence analysis. React Flow receives a read-only UI projection.

### Typed claims

Machine-verifiable claims must use typed fields in addition to a human-readable summary. At minimum, a factual claim contains:

```text
subject
predicate
value
unit
valid_at or valid_during
scope
human_summary
```

Contradiction and constraint checks operate on typed fields, not semantic comparison of prose alone.

### Evidence identity and lineage

Evidence identity must include the canonical upstream source, MCP server and tool, normalized call arguments, retrieval time, observation or data version when available, raw-response hash, transformation lineage, and whether the record is externally observed or agent-derived. Different endpoints backed by the same upstream dataset do not count automatically as independent sources.

### Provider-neutral model access

All LLM calls go through an application-owned model adapter. Groq with `openai/gpt-oss-120b` is the initial adapter implementation. Agent, Parliament, and Jury domain code must not import provider-specific clients directly.

The adapter must expose structured-output validation, tool-call capability, retry and timeout behavior, and usage metadata consistently. Provider capability contract tests are required.

### Safe execution boundary

Every MCP write requires:

```text
freshness revalidation
policy-based approval
idempotency key
immutable execution audit record
duplicate-execution protection
explicit failure or compensation status
```

No side effect may occur in a resumable workflow node unless it is idempotent or protected by an execution ledger.

### Bounded autonomy

Every Parliament → Jury → investigation loop must define maximum cycles, model/tool budget, deadline, repeated-evidence detection, no-feasible-plan handling, and escalation behavior. Reaching a bound produces ESCALATE; it must not silently approve or loop forever.

### MVP frontend

Use React + Vite for the MVP client. The application is currently an interactive decision-process viewer and does not require server-side rendering. Reconsider a full-stack frontend framework only when SSR, frontend server routes, or framework-specific authentication becomes a concrete requirement.

## 2.2 Approved Domain Schema Decision

Use a normalized, versioned PostgreSQL schema for multi-SKU, multi-warehouse, time-bucketed planning.

```text
Organization
├── SKU
├── Warehouse
└── Supplier

PlanningRun
├── PlanningBucket
├── DemandForecast
├── InventorySnapshot
├── SupplierOffer
├── TransportLane
└── CandidatePlan
    ├── ProcurementLine
    └── DistributionLine
```

Schema rules:

- Every operational fact includes `sku_id`, `warehouse_id`, and a validity interval where applicable.
- The MVP uses one calendar day per planning bucket in the organization's timezone. Persist bucket boundaries as UTC timestamps and retain the originating timezone on the planning run.
- `PlanningRun` records its horizon, bucket duration, timezone, and immutable input-data version so a decision can be reproduced.
- Do not create fixed columns such as `day_1`, `day_2`, or `warehouse_1`.
- Quantities include a unit of measure. Persist exact decimal business quantities and convert them to documented integer base units at the OR-Tools boundary.
- Forecasts, offers, and inventory inputs are versioned per planning run. A later source refresh creates a new version rather than rewriting the evidence behind an earlier decision.
- Use JSONB for raw source payloads and optional metadata only. Canonical quantities, prices, identifiers, time bounds, and relationships use typed relational columns.
- The model must permit a future planning run to select hourly or weekly buckets without changing the core schema.

## 2.3 Approved Perishable Inventory Decision

Represent physical perishable inventory at lot level and allocate it using FEFO (First Expired, First Out).

Canonical `InventoryLot` data includes:

```text
lot_id
sku_id
warehouse_id
received_at
manufactured_at (optional)
expires_at
expiry_kind: use_by | best_before
initial_quantity
unit_of_measure
status: available | reserved | quarantined | expired | depleted
source_reference
```

Record quantity changes in an append-only `InventoryMovement` ledger. Movement types include receipt, reservation, release, shipment, transfer, waste, and adjustment. Each movement carries its quantity, occurrence time, and business reference. Current balances may be transactionally materialized for performance, but the movement ledger is authoritative and auditable.

Rules:

- The allocator consumes eligible lots in FEFO order unless a typed constraint provides a documented exception.
- Reserved, quarantined, expired, depleted, and available quantities are never conflated.
- Warehouse redistribution identifies the exact source lots and records paired transfer movements.
- Purchase orders represent expected receipts. Create the observed inventory lot and its authoritative expiry timestamp only when goods are received.
- A supplier's expected shelf life is planning evidence, not an observed lot expiry date.
- Planning snapshots reference versioned lot balances and do not rewrite movement history.
- Store expiry timestamps in UTC and retain the warehouse timezone needed for local display and end-of-day expiry rules.
- Preserve `best_before` versus `use_by` so safety policy and waste policy can treat them differently.

## 2.4 Approved Optimization Policy

Use feasibility-first optimization, lexicographic demand fulfillment, and solver-generated Pareto alternatives.

### Stage 1 — Best attainable fulfillment

Minimize:

```text
Σ shortage_quantity[sku, warehouse, bucket]
  × demand_priority[sku, warehouse]
  × time_urgency[bucket]
```

Demand is not universally hard because real inputs may make complete fulfillment impossible. Explicit contractual or safety-critical minimum service levels remain hard constraints. Record the result as `FULLY_FEASIBLE`, `PARTIALLY_FULFILLED`, or `INFEASIBLE`; never hide a shortage inside a nominally successful plan.

### Stage 2 — Trade-off alternatives

Lock the optimal Stage 1 shortage value, or an explicitly configured tolerance, and generate a small non-dominated set across:

```text
total landed cost
expected waste value
supplier and delivery risk
internal redistribution effort
inventory holding cost
supplier concentration
```

Parliament compares these feasible alternatives. It does not invent an unvalidated allocation.

### Hard constraints

Enforce deterministically:

- inventory-flow conservation by SKU, warehouse, and bucket;
- allocation only from available lots or receipts that have arrived;
- exclusion of quarantined, depleted, and expired lots;
- `use_by`, minimum remaining shelf-life, and FEFO rules;
- supplier availability, capacity, and SKU eligibility;
- lead times, delivery windows, and arrival-bucket availability;
- pack sizes, minimum order quantities, and integer solver base units;
- warehouse volume, weight, and temperature-zone capacity;
- transport-lane capacity and eligibility;
- unit-of-measure compatibility;
- explicit budget ceilings and contractual service minimums;
- organization and warehouse access boundaries; and
- non-negative inventory, procurement, transfer, allocation, waste, and shortage variables.

An agent may propose a hard constraint, but the optimizer accepts it only after it is validated against typed policy or external evidence. Unvalidated preferences remain soft arguments.

## 2.5 Approved Parliament Aggregation Policy

Parliament selects among solver-generated Pareto alternatives using deterministic, versioned role scorecards and minimax regret. Agents do not invent numeric utility scores or directly set solver weights.

Process:

1. OR-Tools produces approximately three to seven non-dominated alternatives.
2. Deterministic code computes normalized metrics for every alternative.
3. Each role evaluates its assigned metrics: Demand uses fulfillment, critical shortage, and resilience; Cost uses landed and holding cost; Freshness uses remaining shelf life and spoilage exposure; Logistics uses lateness, transfers, and capacity slack; Supplier uses reliability, concentration, and capacity risk; Waste uses expected expired quantity and value.
4. LLM agents explain objections, challenge evidence, and propose typed acceptable bounds. A bound changes solver behavior only after deterministic validation against policy or external evidence.
5. A validated change may trigger another solver run.
6. Select the alternative with the smallest maximum role regret, where `regret(role, plan) = best_available_role_score - plan_role_score`.

Governance rules:

- Score formulas and normalization ranges are versioned and stored with the planning run.
- Roles have equal influence by default. Only an explicit organization policy may configure role priorities; agents cannot change their own influence.
- A veto requires a verified hard-constraint violation.
- Preserve every score, ranking, objection, concession, regeneration, and selection in the audit trail.
- Break ties by lowest maximum regret, then lowest total regret, critical shortage, expected waste value, landed cost, and finally stable plan ID.

## 2.6 Approved Decision Integrity Policy v1

Decision Integrity is a deterministic, versioned score combined with non-negotiable gates. A high numeric score never overrides a failed gate.

Calculate component scores from 0 to 100:

```text
Integrity v1 =
    20% critical-claim coverage
  + 20% evidence independence
  + 15% provenance completeness
  + 15% evidence freshness
  + 10% canonical source diversity
  + 10% contradiction resolution
  + 10% dissent robustness
```

Definitions:

- Coverage measures whether material claims have admissible evidence.
- Independence counts effective canonical upstream source groups, not agents or endpoints.
- Provenance measures required lineage completeness and traceability.
- Freshness applies predicate-specific age requirements.
- Source diversity measures distinct canonical source types.
- Contradiction resolution requires contradictions to be absent or explicitly resolved using stronger evidence.
- Dissent robustness requires completion of the approved adversarial checks. A bare statement that Dissent found nothing earns no credit.

Critical-claim materiality is calculated by versioned deterministic policy using claim type and plan exposure. LLM agents cannot assign claim importance.

Threshold policy:

```text
85–100  APPROVE-ELIGIBLE
60–84   INVESTIGATE
0–59    INVESTIGATE while investigation budget remains;
        otherwise ESCALATE
```

`APPROVE-ELIGIBLE` becomes `APPROVE` only when every applicable gate passes.

Hard gates:

- solver infeasibility or a hard-constraint violation produces `REJECT`;
- an unresolved high-severity contradiction on a critical claim produces `INVESTIGATE`;
- a critical claim without external support produces `INVESTIGATE`;
- stale required execution data produces `INVESTIGATE`;
- exhausted autonomy bounds with unresolved uncertainty produce `ESCALATE`;
- an action above the human-approval policy produces `ESCALATE`; and
- strong evidence that establishes proposal invalidity produces `REJECT`.

Persist the integrity-policy version, component scores, per-claim contributions, gate results, thresholds, calculation time, implementation version, final state, and reason codes. Changing a formula, weight, threshold, or gate requires a new policy version. Historical evaluations retain their original version.

Display agent consensus separately with zero weight in Integrity v1. Agreement is a Parliament property, not evidence quality.

## 2.7 Approved Evaluation Ground Truth

Use versioned golden scenarios, generated invariant testing, and an independent oracle.

Each immutable scenario bundle separates:

```text
manifest
true_world_state
agent_visible_observations
expected_evidence_lineage
expected_outcomes
intervention_sequence
```

The system under test receives only agent-visible observations. Ground truth includes canonical source groups, derivations, contaminations, contradictions, feasible status, acceptable objective bounds, expected Jury state, gate results, reason codes, and the facts revealed by each investigation.

Validate proposed plans with a deterministic constraint verifier implemented independently from the optimizer model. Confirm small golden scenarios by exhaustive enumeration. For larger generated cases, use OR-Tools plus the independent verifier. When multiple plans are valid, compare feasibility and objective regret rather than requiring one plan ID.

The initial golden suite covers genuine independent consensus, shared-source false consensus, an agent echo chain, a stale lead-time contradiction, clean current MCP evidence, genuine objective conflict, no fully feasible plan, a FEFO failure, a warehouse-capacity conflict, and duplicate execution retry.

Measure constraint correctness, fulfillment gap, cost/waste regret, provenance completeness, independence-group precision/recall, contradiction precision/recall, Jury state and reason-code accuracy, replanning improvement, and execution idempotency separately.

## 2.8 Approved Dissent Isolation Policy

Dissent receives independent, read-only tool access through a clean-room execution context.

Rules:

- Use a separate model thread, memory namespace, tool-result cache namespace, and tool-call budget from Parliament.
- Dissent cannot invoke MCP write tools.
- In phase one, provide the candidate actions, typed claims requiring verification, applicable policy, and available tool catalog, but withhold Parliament conversation, conclusions, and raw evidence values. Dissent records its investigation plan before retrieval.
- Dissent performs fresh read calls and creates its own evidence records.
- In phase two, reveal the existing evidence graph so deterministic code can compare origins, values, timestamps, and contradictions.
- A fresh query to the same canonical upstream source improves freshness but does not create independent support.
- Preserve the investigation plan, calls, results, comparisons, and any unavailable checks in the audit trail.
- If required Dissent checks cannot run, dissent robustness receives no credit and the applicable Integrity gate fails closed.

## 2.9 Approved Execution Freshness Policy v1

At the instant of an MCP write attempt, required inputs must be no older than:

```text
inventory lot balances and reservations   2 minutes
warehouse remaining capacity              2 minutes
supplier availability and capacity       10 minutes
supplier lead times and delivery windows 10 minutes
prices and offers                         10 minutes and before valid_until
transport availability and capacity      10 minutes
demand forecast                            6 hours
product and shelf-life reference data     24 hours
organization policy                       exact approved version
```

Predicate-specific policy may impose a shorter TTL. Measure age at the write attempt, not when planning began.

The final gate refreshes mutable inputs concurrently, records them as new evidence, reruns deterministic feasibility and Integrity gates, and verifies that the action remains within the approved total and policy limits. Local inventory reservations and capacity checks occur in the protected execution transaction. Use remote quote or reservation tokens and their expiry when an MCP provider supports them.

Proceed only if the exact approved action remains feasible. A required plan-content change, cost above the approved ceiling, expired token, failed refresh, or unresolved material change produces `INVESTIGATE` or policy-required `ESCALATE`; never execute using stale fallback data.

## 2.10 Approved Database Access and Migration Tooling

Use SQLAlchemy 2.x typed mappings with its asyncio API, psycopg 3 as the PostgreSQL driver, and Alembic for schema migrations.

- Domain models remain independent of ORM classes. Repositories and explicit units of work translate between them.
- Use one `AsyncSession` per request or workflow unit of work; never share one session across concurrent tasks.
- Use database constraints for uniqueness, foreign keys, non-negative values, idempotency keys, and ledger integrity.
- Execution paths lock or atomically reserve affected inventory and capacity rows and append ledger/audit records in the same transaction.
- Alembic migrations are reviewed, committed, transactional where PostgreSQL permits, and maintain a single head.
- Use expand/contract migrations for production-compatible changes. Do not rely on destructive downgrade as a production rollback strategy.
- Never run ORM `create_all` in application startup or production. Tests may use it only for isolated unit fixtures; integration tests apply migrations from an empty database.

## 2.11 Approved Test Tooling and Strategy

Use pytest, Hypothesis, and pytest-asyncio in strict mode.

Test layers:

```text
unit          deterministic formulas, schemas, graph rules, scorecards
property      conservation, monotonicity, FEFO, independence, idempotency
golden        versioned end-to-end evaluation scenarios
integration   migrated PostgreSQL and simulated MCP boundaries
contract      model-adapter and MCP response contracts
live          opt-in provider smoke tests only
```

Use deterministic fake model and MCP adapters for required CI tests. Live provider calls are never required for the default suite. Hypothesis failures must retain reproduction information. Run integration tests against an ephemeral migrated PostgreSQL instance, not an in-memory substitute. Coverage is diagnostic; invariant and scenario correctness take precedence over a vanity percentage.

## 2.12 Approved Package Management

Use uv for Python and pnpm for JavaScript/TypeScript.

- Commit `pyproject.toml`, `uv.lock`, and `.python-version`; pin the supported Python 3.12 line.
- Commit `pnpm-workspace.yaml`, `pnpm-lock.yaml`, and the `packageManager` field with an exact pnpm version.
- Use uv and pnpm workspaces if the repository contains multiple packages or apps.
- CI and container builds use frozen lockfiles (`uv sync --frozen` and `pnpm install --frozen-lockfile`).
- Run Python commands through `uv run` and frontend commands through pnpm scripts.
- Do not maintain parallel pip requirements files or npm/yarn lockfiles unless they are generated export artifacts with a documented consumer.
- Dependency updates intentionally modify and review the relevant lockfile.

---

# 3. Agent Roles

## 3.1 Planner Agent

The Planner receives a high-level objective and decomposes it into tasks.

Example:

```text
Goal:
Satisfy 7-day demand while minimizing cost and waste.

Possible plan:
1. Retrieve demand
2. Retrieve inventory
3. Check warehouse capacity
4. Retrieve suppliers
5. Compare pricing
6. Check lead times
7. Check perishability
8. Generate procurement proposals
9. Run Parliament
10. Run Jury
11. Replan if necessary
12. Execute final procurement
```

The Planner must be capable of changing the plan based on Jury feedback.

---

# 4. Parliament

Parliament is the multi-agent decision-making layer.

Do NOT implement Parliament as several identical LLM calls that vote on the same answer.

Each agent must have a distinct objective.

## 4.1 Demand Agent

Objective:

> Minimize the probability of stockouts while satisfying expected demand.

Focus:

```text
forecast
historical demand
location demand
required quantity
```

---

## 4.2 Cost Agent

Objective:

> Minimize total procurement cost.

Focus:

```text
unit price
bulk discounts
transport cost
supplier price
```

---

## 4.3 Freshness Agent

Objective:

> Minimize spoilage caused by excessive or poorly timed procurement.

Focus:

```text
shelf life
arrival time
consumption rate
expected leftover inventory
```

---

## 4.4 Logistics Agent

Objective:

> Minimize delivery and transportation risk.

Focus:

```text
lead time
delivery windows
warehouse capacity
transport constraints
```

---

## 4.5 Supplier Agent

Objective:

> Prefer reliable suppliers while satisfying the procurement requirement.

Focus:

```text
availability
historical fulfillment
lead time
supplier reliability
```

---

## 4.6 Waste Agent

Objective:

> Minimize unnecessary excess inventory.

Focus:

```text
expected consumption
forecast uncertainty
inventory surplus
spoilage risk
```

---

# 5. Agent Proposal Contract

Every Parliament agent must return structured output.

Example:

```json
{
  "agent_id": "cost_agent",
  "proposal": {
    "supplier_allocations": [
      {
        "supplier_id": "S1",
        "quantity": 300
      }
    ],
    "warehouse_allocations": {
      "W1": 150,
      "W2": 150
    }
  },
  "objective": "minimize_cost",
  "claims": [
    "C17",
    "C21"
  ],
  "constraints": [
    "C5"
  ],
  "confidence": 0.82,
  "reasoning_summary": "Supplier S1 has the lowest verified unit price."
}
```

Do not rely on unstructured natural-language outputs when information needs to be compared by the system.

---

# 6. Parliament Negotiation

Negotiation happens in rounds.

## Round 1 — Independent proposals

Each agent proposes what it believes should happen.

Example:

```text
Demand:
500 units

Cost:
300 units

Freshness:
350 units

Logistics:
250 local + 100 redistribution

Supplier:
Prefer Supplier A

Waste:
320 units
```

---

## Round 2 — Challenge

Each agent receives competing proposals.

It should identify:

```text
constraint conflicts
objective conflicts
missing evidence
proposal weaknesses
```

---

## Round 3 — Revision

Agents may change their proposals.

Every major change must include:

```text
what changed
why it changed
supporting evidence
```

Example:

```json
{
  "change": "quantity 500 -> 350",
  "reason": "freshness constraint",
  "evidence": ["E17"]
}
```

---

# 7. Parliament Decision

Parliament produces one candidate procurement plan.

Example:

```json
{
  "proposal_id": "P14",
  "procurement": [
    {
      "supplier_id": "S1",
      "quantity": 250
    },
    {
      "supplier_id": "S2",
      "quantity": 100
    }
  ],
  "distribution": {
    "W1": 150,
    "W2": 100,
    "W3": 100
  },
  "supporting_claims": [
    "C4",
    "C11",
    "C18"
  ],
  "participating_agents": [
    "demand",
    "cost",
    "freshness",
    "logistics",
    "supplier",
    "waste"
  ]
}
```

The proposal is NOT automatically executable.

It must pass through the Jury.

---

# 8. Evidence System

Evidence is central to Civitas.

Every important claim should have identifiable provenance.

Minimum evidence fields:

```text
evidence_id
source_id
source_type
agent_id
content
timestamp
claim_id
derived_from
```

Example:

```json
{
  "evidence_id": "E17",
  "source_id": "SUPPLIER_API_04",
  "source_type": "mcp",
  "agent_id": "supplier_agent",
  "content": "Supplier S1 lead time is 3 days.",
  "timestamp": "2026-08-27T10:32:00Z",
  "derived_from": []
}
```

---

# 9. Evidence Graph

The graph connects:

```text
MCP Tool Call
      ↓
Source
      ↓
Evidence
      ↓
Claim
      ↓
Agent
      ↓
Proposal
      ↓
Decision
```

Supported node types:

```text
Agent
Claim
Evidence
Source
MCP Call
Proposal
Decision
```

Supported relationships:

```text
PRODUCED
SUPPORTS
DERIVED_FROM
RETRIEVED_FROM
CONTRADICTS
DEPENDS_ON
USED_IN
```

---

# 10. Critical Distinction

The system MUST distinguish:

### External evidence

```text
MCP response
database record
supplier record
document
```

from:

### Agent-derived information

```text
Agent A concluded X.

Agent B reads Agent A's conclusion.
```

Agent B's agreement does NOT count as an independent source of evidence.

This is one of the central design principles of Civitas.

---

# 11. Agent Jury

The Jury evaluates the candidate Parliament proposal.

It must answer:

```text
1. Where did the supporting claims originate?
2. How many genuinely independent sources support them?
3. Are several agents relying on the same evidence?
4. Did one agent's conclusion become another agent's evidence?
5. Is there contradictory evidence?
6. Can the proposal survive deliberate dissent?
```

---

# 12. Independence Analyzer

The initial MVP should use explicit graph-based heuristics.

Examples:

## Case A — Independent evidence

```text
Agent A → Source X
Agent B → Source Y
Agent C → Source Z
```

Result:

```text
High independence
```

---

## Case B — Shared source

```text
Agent A → Source X
Agent B → Source X
Agent C → Source X
```

Result:

```text
Low independence
```

---

## Case C — Agent echo

```text
Agent A → Claim C1
Agent B → C1
Agent C → Agent B
Agent D → Agent C
```

Result:

```text
Very low independent support
```

The implementation must not count every agent as an independent vote.

---

# 13. Dissent Agent

The Dissent Agent has a deliberately different objective.

It receives:

```text
candidate proposal
claims
evidence graph
agent arguments
```

Its instruction is essentially:

> **Assume this proposal is wrong. Find the strongest evidence that could invalidate it.**

It should investigate:

```text
contradictions
stale information
missing constraints
alternative suppliers
unexpected demand
capacity conflicts
supplier failures
```

Example:

```json
{
  "supports_dissent": true,
  "contradictions": [
    {
      "claim_id": "C17",
      "reason": "Current supplier record reports 10-day lead time."
    }
  ],
  "severity": "high"
}
```

---

# 14. Decision Integrity

The Jury produces a **Decision Integrity Score**.

Do NOT treat this as generic LLM confidence.

The score should be based on system-observable properties.

Initial factors:

```text
consensus strength
evidence independence
source diversity
provenance completeness
contradictions
dissent findings
```

Example:

```text
Consensus:              5 / 6
Independent sources:    2
Shared dependencies:    HIGH
Contradictions:         1
Dissent:                FOUND

Decision Integrity:
41 / 100

Status:
INVESTIGATE
```

The score must always include explanatory factors.

---

# 15. Decision States

The Jury returns one of:

## APPROVE

The proposal has sufficient evidence integrity.

The Planner may proceed to execution.

## INVESTIGATE

The evidence is insufficient, highly correlated, or contradictory.

The Planner must create additional investigation tasks.

## ESCALATE

The decision is high-impact and unresolved uncertainty remains.

Human approval may be required.

## REJECT

The proposal violates important constraints or evidence strongly indicates it is incorrect.

---

# 16. Jury → Planner Feedback

The Jury must provide structured feedback.

Example:

```json
{
  "status": "INVESTIGATE",
  "reasons": [
    "Three agents depend on the same supplier record.",
    "Supplier lead time has conflicting values."
  ],
  "required_investigation": [
    "Verify current supplier lead time."
  ]
}
```

The Planner converts this into a new task.

Example:

```text
Jury:
VERIFY SUPPLIER LEAD TIME

Planner:
1. Query current supplier endpoint
2. Query recent fulfillment history
3. Recalculate feasible procurement
4. Reopen Parliament
```

---

# 17. MCP Integration

MCP is the operational interface between agents and the procurement environment.

The system should use the participating partner's MCP server whenever possible.

Possible operations:

```text
get_inventory()
get_demand()
get_supplier_data()
get_supplier_prices()
get_lead_times()
get_warehouse_capacity()
get_product_details()
get_order_history()
create_procurement_order()
update_inventory()
```

The actual available MCP tools depend on the partner.

Every MCP result used in reasoning should be recorded as evidence.

---

# 18. Execution Model

Civitas should never allow the LLM itself to directly decide that execution has happened.

The flow should be:

```text
LLM proposes action
       ↓
Jury validates decision
       ↓
Planner authorizes execution path
       ↓
MCP execution tool
       ↓
Operational state changes
```

This gives a clear separation between:

```text
reasoning
decision
execution
```

---

# 19. Primary Demo Scenario

The demo should use a procurement problem containing:

```text
multiple warehouses
multiple suppliers
different prices
different lead times
different demand levels
shelf-life differences
transport constraints
```

Example:

```text
Goal:
Satisfy the next 7 days of demand
while minimizing cost and waste.
```

---

# 20. Deliberate Failure Scenario

Construct a safe, controlled scenario where a misleading piece of information causes apparent consensus.

Example:

```text
Supplier A:
3-day lead time
```

Several agents consume that same information.

The Parliament reaches:

```text
5 / 6 agents recommend Supplier A
```

The system should initially appear ready to execute.

Then Agent Jury analyzes the evidence.

It discovers:

```text
3 agents rely on same source
1 agent copied another agent's conclusion
Current supplier record contradicts the original value
```

The Jury produces:

```text
Decision Integrity: 38 / 100

ACTION:
INVESTIGATE
```

---

# 21. Dissent Investigation

Dissent Agent checks current information through MCP.

Example:

```text
Historical source:
3-day lead time

Current operational source:
10-day lead time
```

Now the system has a real contradiction.

The original Parliament proposal cannot safely proceed.

---

# 22. Adaptive Replanning Demo

The Planner receives:

```text
JURY:
Supplier A lead time is unreliable.
```

It creates a new plan:

```text
1. Verify Supplier B
2. Verify Supplier C
3. Recalculate supply gap
4. Reopen Parliament
```

Parliament negotiates again.

New proposal:

```text
Supplier B → 200
Supplier C → 100
Internal redistribution → 50
```

---

# 23. Final Jury Decision

The Jury runs again.

Example:

```text
Consensus:              6 / 6
Independent sources:    5
Shared dependencies:    LOW
Contradictions:         0
Dissent:                RESOLVED

Decision Integrity:
91 / 100

STATUS:
APPROVE
```

---

# 24. Real Execution

The execution agent uses MCP:

```text
create_procurement_order()
update_inventory_allocation()
```

The system displays:

```text
PROCUREMENT PLAN APPROVED

Supplier B:
200 units

Supplier C:
100 units

Internal redistribution:
50 units

MCP execution:
SUCCESS
```

The demo therefore proves:

```text
PLAN
→ INVESTIGATE
→ DEBATE
→ VALIDATE
→ REPLAN
→ EXECUTE
```

---

# 25. HalluSquatting-Inspired Scenario

A secondary demonstration may simulate the broader mechanism from HalluSquatting.

The research demonstrates that an agent can hallucinate a resource identifier, retrieve an external resource, absorb information from it, and eventually invoke tools based on that information.

Civitas should not reproduce a real attack.

Instead, simulate:

```text
Agent A:
incorrectly identifies a resource

      ↓

Agent B:
uses A's claim

      ↓

Agent C:
uses B's conclusion

      ↓

Parliament:
apparent consensus

      ↓

Jury:
shared origin detected
```

The key lesson demonstrated is:

> **Incorrect information can become more convincing as it propagates through an agent network.**

---

# 26. User Interface

The UI should prioritize the autonomous process.

## View 1 — Goal

```text
PROCUREMENT OBJECTIVE

Satisfy 7-day demand
Minimize cost
Minimize waste

[ START ]
```

## View 2 — Parliament

Display:

```text
DEMAND
500 units

COST
300 units

FRESHNESS
350 units

LOGISTICS
250 + redistribution

SUPPLIER
Supplier A

WASTE
320 units
```

Show negotiation rounds.

## View 3 — Jury

Display:

```text
AGENT JURY

Consensus             5 / 6
Independent evidence  2
Shared dependencies   HIGH
Contradictions        1
Dissent               FOUND

Decision Integrity    41 / 100

→ INVESTIGATE
```

## View 4 — Evidence Graph

Visualize:

```text
Source
 ↓
Evidence
 ↓
Claim
 ↓
Agent
 ↓
Proposal
```

Highlight shared dependencies.

## View 5 — Final Execution

```text
DECISION APPROVED

Integrity: 91 / 100

MCP ACTION:
PROCUREMENT ORDER CREATED ✓
```

---

# 27. MVP

The MVP must contain:

```text
[ ] Planner Agent
[ ] At least 4 specialized Parliament agents
[ ] Partner MCP integration
[ ] MCP-driven data retrieval
[ ] Structured agent proposals
[ ] Parliament negotiation
[ ] Evidence normalization
[ ] Evidence graph
[ ] Independence analysis
[ ] Dissent Agent
[ ] Decision Integrity Score
[ ] Jury → Planner feedback
[ ] Replanning
[ ] Real MCP execution
[ ] Visual evidence graph
```

---

# 28. Stretch Goals

Only implement these after the MVP works.

## Temporal Evidence

Detect stale information.

## Contamination Tracking

Track how an incorrect claim propagates through agents.

## Information-Gain Investigation

Select the next investigation based on which action reduces uncertainty most.

## Long-Term Belief Tracking

Track how agent beliefs change over time.

## Multi-Round Parliament

Allow repeated negotiation after every Jury cycle.

## Human Approval

Require approval for high-value or high-risk procurement.

## MCP Tool Abstraction

Expose Civitas as a reusable middleware layer for other agent systems.

---

# 29. Evaluation

Create controlled scenarios.

## Test A — Genuine Consensus

Different agents use genuinely independent sources.

Expected:

```text
High integrity
APPROVE
```

## Test B — Shared Evidence

All agents rely on the same source.

Expected:

```text
Low independence
INVESTIGATE
```

## Test C — Agent Echo

Agents repeatedly inherit prior agent conclusions.

Expected:

```text
Very low independent support
```

## Test D — Contradiction

Parliament agrees, but a current source contradicts the proposal.

Expected:

```text
DISSENT FOUND
REPLAN
```

## Test E — Clean MCP Data

Independent current MCP results support the plan.

Expected:

```text
High integrity
EXECUTE
```

## Test F — Genuine Objective Conflict

Agents disagree for legitimate reasons.

Expected:

```text
Parliament negotiates
Jury does not treat disagreement itself as failure
```

---

# 30. Engineering Principles

## Prefer deterministic logic where possible

Use normal code for:

```text
graph traversal
dependency detection
source matching
score calculation
constraint checking
```

Use LLMs for:

```text
interpretation
proposal generation
argumentation
dissent
natural-language reasoning
```

Do not use an LLM for something that can be reliably determined from structured data.

---

## Preserve provenance

Never discard:

```text
source
timestamp
agent
claim
derived_from
MCP call
```

Once provenance is lost, Jury cannot determine independence.

---

## Keep agents modular

Every agent should be replaceable.

Avoid putting Parliament logic, Jury logic, MCP logic, and business logic into one giant agent.

---

## Prefer structured communication

Agents should communicate through explicit proposal and claim objects rather than long natural-language conversations wherever possible.

---

# 31. What NOT to Claim

Do not claim:

```text
"We guarantee correct decisions."

"We completely solve hallucinations."

"We mathematically prove evidence independence."

"Our Jury cannot be wrong."
```

Instead say:

```text
"We identify evidence dependencies."

"We detect apparent consensus supported by shared information."

"We deliberately challenge collective decisions."

"We provide an interpretable decision-integrity assessment."

"We trigger investigation when evidence is insufficient."
```

This makes the system technically defensible.

---

# 32. Core Differentiation

Civitas is not just:

### An autonomous procurement agent

That is the baseline challenge.

### A multi-agent optimizer

The Parliament is specifically designed around conflicting objectives.

### An LLM-as-a-Judge

The Jury does not primarily evaluate whether the final answer sounds correct.

It evaluates:

```text
evidence
provenance
dependencies
independence
contradictions
dissent
```

### A security product

Security is one possible source of information failure.

The core problem is broader:

> **Collective decision integrity in autonomous multi-agent systems.**

---

# 33. Design Philosophy

The system should embody three principles:

### 1. Disagreement is useful

Agents should not be forced toward consensus too early.

### 2. Consensus requires evidence

More agents agreeing should not automatically increase confidence.

### 3. Uncertainty should trigger action

When the Jury finds insufficient evidence, the system should investigate or replan rather than simply produce a lower-confidence answer.

---

# 34. Final End-to-End Example

```text
USER
"Procure enough inventory for the next 7 days
while minimizing cost and waste."

        ↓

PLANNER

        ↓

MCP DATA

        ↓

PARLIAMENT

Demand:
500

Cost:
300

Freshness:
350

Logistics:
250 + redistribution

Supplier:
Supplier A

Waste:
320

        ↓

NEGOTIATION

        ↓

PROPOSAL

350 units
Supplier A + B

        ↓

JURY

Consensus: 5/6
Independent evidence: 2
Shared evidence: HIGH
Contradiction: FOUND

        ↓

INTEGRITY: 41/100

        ↓

REPLAN

"Verify current supplier lead time."

        ↓

MCP

        ↓

CURRENT DATA

Supplier A:
10-day lead time

Supplier B:
3-day lead time

        ↓

PARLIAMENT REOPENS

        ↓

NEW PROPOSAL

200 Supplier B
100 Supplier C
50 Internal redistribution

        ↓

JURY

Integrity: 91/100

        ↓

APPROVE

        ↓

MCP

create_procurement_order()

        ↓

EXECUTED
```

---

# 35. Final Product Definition

> **Civitas is an autonomous procurement system where specialized agents with conflicting objectives negotiate decisions through an Agent Parliament, while an evidence-aware Agent Jury examines the independence and integrity of the reasoning behind their consensus and can trigger further investigation and replanning before execution.**

---

# 36. Final Product Principle

```text
PARLIAMENT:
"What should we do?"

JURY:
"Why do we believe that?"

PLANNER:
"What should we investigate next?"

MCP:
"What is actually happening in the world?"

EXECUTION:
"Now act."
```

The goal is not to create agents that merely cooperate.

The goal is to create an autonomous system that can **reason collectively, challenge itself, adapt its plan, and act only when its decision is sufficiently supported by evidence.**
