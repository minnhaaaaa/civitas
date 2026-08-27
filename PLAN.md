# Agent Parliament + Agent Jury

## Autonomous Food Procurement, Negotiation & Evidence-Aware Decision System

---

# 1. Project Overview

This project extends **Problem Statement 5: Autonomous Food Demand, Procurement & Distribution Agent** into a more advanced multi-agent decision system.

The official problem asks an autonomous agent to determine:

- what food should be procured
- how much should be procured
- which suppliers should be used
- how inventory should be distributed
- while considering demand, inventory, supplier lead times, pricing, perishability, and location requirements.

The project goes beyond this by introducing two new architectural layers:

### Agent Parliament

Multiple specialized agents represent **different objectives and interests** rather than simply collaborating toward the same answer.

They negotiate competing proposals to produce a procurement plan.

### Agent Jury

The Jury evaluates whether the reasoning behind the Parliament's agreement is actually supported by sufficiently independent evidence.

It does not simply ask:

> "Do most agents agree?"

It asks:

> **"Why do they agree, and is that agreement genuinely supported by independent evidence?"**

---

# 2. Core Idea

Traditional multi-agent architecture:

```text
User
 ↓
Multiple Agents
 ↓
Consensus
 ↓
Decision
 ↓
Action
```

Our architecture:

```text
User
 ↓
Planner
 ↓
Agent Parliament
 ↓
Conflicting proposals
 ↓
Negotiation
 ↓
Proposed procurement plan
 ↓
Agent Jury
 ↓
Evidence + provenance + independence + dissent
 ↓
Decision Integrity
 ├── APPROVE → Execute
 └── INVESTIGATE → Replan → Parliament
```

The fundamental thesis is:

> **A unanimous group of agents can still be wrong if their agreement comes from shared or unreliable information.**

---

# 3. Inspiration and Technical Motivation

The uploaded HalluSquatting research demonstrates an important agent-specific failure chain:

```text
LLM hallucination
        ↓
incorrect resource identifier
        ↓
external resource retrieval
        ↓
poisoned context
        ↓
tool invocation
        ↓
action
```

The work shows that hallucinated resource identifiers can transfer across models and applications and ultimately influence agent tool execution.

The key insight we take from this is broader than the specific security attack:

> **Information can enter an agentic system at one point and become an input to downstream decisions and actions.**

In a multi-agent system, this can become:

```text
questionable evidence
        ↓
Agent A
        ↓
claim
        ↓
Agent B
        ↓
Agent C
        ↓
apparent consensus
        ↓
real-world action
```

Agent Jury is designed to detect this type of **information propagation and correlated agreement** before a consequential procurement decision is executed.

The agentic-AI survey supplied with the project identifies multi-agent coordination problems including inconsistent shared context, communication failures, correlated failures, and cascading system failures.

---

# 4. Problem Statement

Food procurement involves several competing objectives.

A company may want to:

- satisfy future demand
- minimize procurement cost
- reduce food waste
- maintain sufficient safety stock
- select reliable suppliers
- respect warehouse capacity
- minimize transportation cost
- meet delivery deadlines

These objectives can conflict.

A conventional autonomous agent may collapse them into one optimization function.

A conventional multi-agent system may ask several agents for recommendations and choose the majority answer.

Both approaches have weaknesses.

Agents can:

- prioritize different objectives
- depend on different information
- inherit each other's conclusions
- rely on the same outdated source
- reinforce incorrect assumptions
- produce apparently independent agreement that is not actually independent

Therefore:

> **The system needs both a mechanism for negotiating conflicting objectives and a mechanism for determining whether the resulting agreement is trustworthy.**

---

# 5. Project Objectives

## Primary Objective

Build an autonomous food procurement system in which specialized agents:

1. investigate the environment,
2. propose competing procurement strategies,
3. negotiate those strategies,
4. construct a candidate plan,
5. have the plan evaluated by Agent Jury,
6. replan when evidence integrity is insufficient,
7. execute the approved plan using MCP-connected tools.

---

# 6. Core Innovation

The project introduces two complementary concepts.

## 6.1 Agent Parliament

Agents intentionally have different objectives.

Example:

```text
Demand Agent
    maximize demand fulfillment

Cost Agent
    minimize procurement cost

Freshness Agent
    minimize spoilage

Logistics Agent
    minimize transport and delivery risk

Supplier Agent
    maximize supplier reliability

Waste Agent
    minimize expected wastage
```

These agents are not designed to produce the same answer.

They are designed to **argue for the plan that best satisfies their objective**.

---

## 6.2 Agent Jury

Once Parliament reaches a proposal, Agent Jury examines:

```text
What claims support the proposal?

Where did the claims originate?

Are different agents actually using independent evidence?

Did one agent's conclusion become another agent's evidence?

Are there contradictions?

Can a dedicated dissent agent disprove the proposal?
```

The Jury therefore evaluates the **decision process**, not merely the final answer.

---

# 7. Why Parliament and Jury Are Different

## Parliament asks:

> **What should we do?**

## Jury asks:

> **Do we have enough trustworthy, independent evidence to believe that we should do it?**

This separation is essential.

Without Parliament:

```text
No structured conflict resolution.
```

Without Jury:

```text
No protection against false consensus.
```

Together:

```text
Conflict
 +
Evidence verification
 =
Evidence-backed autonomous decision
```

---

# 8. Target Architecture

```text
                       OPERATOR
                           │
                           ▼
                         CODEX
                           │
                 INBOUND CIVITAS MCP
                           │
                           ▼
                  PROCUREMENT GOAL
                           │
                           ▼
                       PLANNER
                           │
                           ▼
                ┌─────────────────────┐
                │  AGENT PARLIAMENT   │
                └──────────┬──────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   Demand Agent       Cost Agent       Freshness Agent
         │                 │                 │
         ▼                 ▼                 ▼
   Logistics Agent   Supplier Agent     Waste Agent
         │                 │                 │
         └─────────────────┼─────────────────┘
                           │
              OUTBOUND MCP / DATA LAYER
                           │
                           ▼
                    EVIDENCE + CLAIMS
                           │
                           ▼
                    NEGOTIATION ROUNDS
                           │
                           ▼
                   CANDIDATE PROPOSAL
                           │
                           ▼
                   ┌───────────────┐
                   │  AGENT JURY   │
                   └───────┬───────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
      Provenance      Independence       Dissent
       Analysis         Analysis          Agent
          │                │                │
          └────────────────┼────────────────┘
                           │
                           ▼
                 DECISION INTEGRITY
                           │
                 ┌─────────┴─────────┐
                 ▼                   ▼
              APPROVE             INVESTIGATE
                 │                   │
                 ▼                   ▼
              EXECUTE              REPLAN
                                      │
                                      └──────► PARLIAMENT
```

---

# 8.1 Approved Architecture Amendments

These amendments are implementation requirements and take precedence over simplified examples elsewhere in this plan.

1. **Optimizer-first construction:** Parliament agents provide typed objectives, hard-constraint candidates, evidence, challenges, and trade-off preferences. OR-Tools constructs or validates all candidate allocations before Jury review. LLM output alone never determines executable quantities.
2. **Durable evidence source:** PostgreSQL stores the canonical evidence and decision lineage. NetworkX is a rebuildable analysis projection; React Flow is a read-only visualization projection.
3. **Typed factual claims:** claims carry subject, predicate, typed value, unit, temporal validity, scope, and a human summary. Deterministic checks use these fields rather than prose matching.
4. **Canonical evidence identity:** record the upstream source, MCP server/tool, normalized arguments, retrieval time, data version, raw-response hash, transformation lineage, and external-versus-agent-derived classification. Endpoint diversity alone is not evidence independence.
5. **Provider adapter:** domain code invokes an application-owned model interface. Groq with `openai/gpt-oss-120b` is the initial provider implementation and must pass structured-output, tool-use, timeout, retry, and usage-metadata contract tests.
6. **Execution safety:** every MCP write passes freshness revalidation and policy approval, uses an idempotency key, writes an immutable audit record, prevents duplicates, and records failure or compensation state.
7. **Termination policy:** each autonomous loop has cycle, cost/tool, and time limits; detects repeated evidence; handles infeasibility; and escalates when a bound is reached.
8. **Primary product interface:** expose a small intent-level Civitas MCP server to Codex. Codex handles conversation and presentation; Civitas owns workflow state and every deterministic authorization boundary. Use React + Vite only as an optional read-only evidence and execution-audit viewer.

The approved technical decisions and their versioned v1 policies are recorded below. Future changes to these decisions require explicit approval and a new policy or architecture-decision version where indicated.

## 8.2 Decision 1 — Domain Schema (Approved)

Use a normalized, versioned PostgreSQL model for multi-SKU, multi-warehouse, time-bucketed planning.

- Core master data: Organization, SKU, Warehouse, and Supplier.
- Each PlanningRun owns an ordered set of PlanningBuckets plus versioned DemandForecast, InventorySnapshot, SupplierOffer, and TransportLane inputs.
- CandidatePlan owns typed ProcurementLine and DistributionLine records.
- The MVP uses daily calendar buckets in the organization's timezone, stored as UTC interval boundaries. The planning run also stores its timezone, horizon, bucket duration, and immutable input-data version.
- Operational facts carry SKU, warehouse, unit of measure, and temporal scope where applicable. Fixed day or warehouse columns are forbidden.
- Persist exact decimal business quantities. Convert to documented integer base units only at the OR-Tools boundary.
- JSONB is limited to raw source payloads and optional metadata; canonical planning fields and relationships remain typed and relational.
- The same schema must support future hourly or weekly planning runs without a core-table redesign.

## 8.3 Decision 2 — Perishable Inventory (Approved)

Use lot-level inventory, FEFO allocation, and an append-only movement ledger.

- `InventoryLot` identifies the SKU, warehouse, receipt time, optional manufacture time, expiry time, expiry kind (`use_by` or `best_before`), initial quantity, unit, operational status, and source reference.
- `InventoryMovement` records every receipt, reservation, release, shipment, transfer, waste event, or adjustment with a quantity, timestamp, and business reference.
- The movement ledger is authoritative. A transactionally maintained current-balance projection is allowed for performance.
- Eligible stock is allocated in First-Expired-First-Out order unless a typed, documented constraint requires an exception.
- Available, reserved, quarantined, expired, and depleted stock remain separate.
- Transfers retain exact source-lot identity and paired movement records.
- Incoming purchase orders are expected receipts. Actual lot identity and authoritative expiry are recorded at physical receipt.
- Expected supplier shelf life is planning evidence and must not be presented as an observed lot expiry.
- Planning snapshots version lot balances without mutating ledger history.
- Expiry timestamps use UTC while preserving warehouse timezone and `best_before` versus `use_by` semantics.

## 8.4 Decision 3 — Optimization Policy (Approved)

Use a two-stage feasibility-first optimization policy.

1. Minimize priority- and urgency-weighted shortage across SKU, warehouse, and time bucket. Full demand is not universally hard; explicit contractual or safety-critical service minimums are hard.
2. Lock the best attainable fulfillment level, or a configured tolerance, and generate a small Pareto set over landed cost, expected waste value, supplier/delivery risk, redistribution effort, holding cost, and supplier concentration.

Every solve reports `FULLY_FEASIBLE`, `PARTIALLY_FULFILLED`, or `INFEASIBLE`.

Hard constraints cover inventory-flow conservation; lot eligibility and arrival; FEFO, `use_by`, and minimum shelf life; supplier capacity and SKU eligibility; lead times and windows; pack sizes and minimum orders; warehouse volume, weight, and temperature capacity; transport-lane capacity; compatible units; explicit budgets and contractual minimum service; organizational access boundaries; and non-negative integer solver variables.

Agents may suggest constraints, but only typed policy or verified external evidence can promote a suggestion to a solver hard constraint. Parliament receives solver-validated alternatives rather than directly authorizing LLM-generated quantities.

## 8.5 Decision 4 — Parliament Aggregation (Approved)

Use solver-generated Pareto alternatives, deterministic role scorecards, and minimax-regret selection.

- OR-Tools emits approximately three to seven non-dominated plans.
- Versioned code calculates normalized metrics. Demand scores fulfillment, shortage, and resilience; Cost scores landed and holding cost; Freshness scores shelf-life and spoilage exposure; Logistics scores lateness, transfers, and capacity slack; Supplier scores reliability, concentration, and capacity risk; Waste scores expected expired quantity and value.
- LLM agents provide evidence-backed explanations, challenges, and typed acceptable bounds; they do not generate utility scores or directly set solver weights.
- Validated bounds may cause the solver to regenerate alternatives.
- Select the plan that minimizes maximum regret across roles. Roles are equally influential unless a versioned organization policy says otherwise.
- Only verified hard-constraint violations create vetoes.
- Tie-break by total regret, critical shortage, expected waste value, landed cost, and stable plan ID.
- Persist every score, objection, concession, regeneration, and selection for audit and replay.

## 8.6 Decision 5 — Decision Integrity v1 (Approved)

Use a deterministic versioned score with hard decision gates.

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

Component values and critical-claim materiality are deterministic. Consensus is displayed but contributes zero score.

Thresholds are 85–100 `APPROVE-ELIGIBLE`, 60–84 `INVESTIGATE`, and 0–59 `INVESTIGATE` while budget remains or `ESCALATE` after bounds are exhausted. Approval eligibility becomes approval only after all gates pass.

Gates reject solver-infeasible or hard-constraint-violating plans; investigate unresolved high-severity contradictions, unsupported critical claims, or stale execution inputs; escalate exhausted unresolved loops and actions requiring human approval; and reject proposals proven invalid by strong evidence.

Persist policy and implementation versions, component and per-claim contributions, gates, thresholds, timestamps, final state, and reason codes. Any formula, weight, threshold, or gate change creates a new policy version.

## 8.7 Decision 6 — Evaluation Ground Truth (Approved)

Use immutable golden scenario bundles, generated invariant cases, and an independent plan verifier. Each bundle separates hidden true world state from agent-visible observations and includes expected lineage, outcomes, reason codes, and investigation interventions.

Exhaustively enumerate small scenarios to cross-check optimal fulfillment and trade-off bounds. For larger generated cases, use OR-Tools plus an independently implemented constraint verifier. Score any valid equivalent plan by feasibility and regret rather than exact plan identity.

The initial golden suite covers independent consensus, shared evidence, echo propagation, stale contradiction, clean MCP data, genuine objective conflict, partial fulfillment, FEFO failure, capacity conflict, and duplicate execution retry.

## 8.8 Decision 7 — Dissent Isolation (Approved)

Dissent has independent read-only MCP access in a separate model thread, memory namespace, cache namespace, and budget. It first receives candidate actions, typed verification targets, policy, and the tool catalog—not Parliament conversation or raw evidence—and records an investigation plan. It retrieves fresh evidence, then receives the prior graph for deterministic comparison.

A repeated call to the same canonical source improves freshness but not independence. Unavailable required checks are audited, receive no robustness credit, and fail closed. Dissent never receives write tools.

## 8.9 Decision 8 — Execution Freshness v1 (Approved)

Default maximum evidence ages at the write attempt are two minutes for lot balances, reservations, and warehouse capacity; ten minutes for supplier availability/capacity, lead times/windows, offers/prices, and transport capacity; six hours for demand forecasts; and 24 hours for product/shelf-life reference data. Offers must also remain before `valid_until`, and organization policy must match the exact approved version.

The final gate refreshes mutable inputs, records new evidence, reruns feasibility and Integrity, and transactionally protects local reservations and capacity. Execute only if the exact approved action remains feasible and within its approved total. Missing refreshes, expired tokens, material plan changes, or excess cost investigate or escalate; stale fallback execution is forbidden.

## 8.10 Decision 9 — SQLAlchemy and Alembic (Approved)

Use SQLAlchemy 2.x typed async mappings with psycopg 3 and Alembic. Keep domain models ORM-independent behind repositories and explicit units of work. Use one `AsyncSession` per request/workflow unit, enforce integrity in PostgreSQL, and atomically reserve inventory/capacity with ledger and audit writes.

Apply reviewed migrations with a single head and expand/contract production changes. Application startup never calls `create_all`; integration tests migrate an empty PostgreSQL database.

## 8.11 Decision 10 — pytest and Property Tests (Approved)

Use pytest, Hypothesis, and strict-mode pytest-asyncio. Required CI covers deterministic units, generated invariants, golden scenarios, migrated-PostgreSQL integration, and fake-adapter contracts. Live provider smoke tests are opt-in. Preserve Hypothesis reproduction information and prioritize invariants and scenario correctness over raw coverage percentage.

## 8.12 Decision 11 — uv and pnpm (Approved)

Use uv for Python and pnpm for the React client. Commit both ecosystems' manifests, exact lockfiles, `.python-version`, `pnpm-workspace.yaml`, and an exact pnpm `packageManager` field. CI and containers install from frozen lockfiles. Do not mix pip requirements or npm/yarn lockfiles into the authoritative dependency workflow.

---

# 9. System Actors

## 9.1 Planner Agent

Responsible for decomposing the user's high-level procurement objective.

Example:

```text
Goal:
Satisfy 7-day food demand while minimizing cost and waste.

Plan:
1. Retrieve demand
2. Retrieve current inventory
3. Inspect warehouse capacity
4. Retrieve supplier options
5. Compare supplier prices
6. Compare lead times
7. Account for perishability
8. Generate competing procurement proposals
9. Run Parliament negotiation
10. Run Jury evaluation
11. Replan if necessary
12. Execute approved plan
```

The planner is responsible for adapting this sequence based on Jury feedback.

---

# 10. Parliament Agents

## 10.1 Demand Agent

Objective:

> Ensure sufficient inventory to satisfy expected demand.

Inputs:

```text
historical demand
current demand
forecast
location-level demand
```

Output:

```text
required quantity
demand confidence
supporting evidence
```

---

## 10.2 Cost Agent

Objective:

> Minimize total procurement cost.

Considers:

```text
unit price
shipping cost
bulk discounts
supplier price differences
```

---

## 10.3 Freshness Agent

Objective:

> Minimize spoilage and respect shelf-life constraints.

Considers:

```text
remaining shelf life
expected consumption
transport duration
arrival date
```

---

## 10.4 Logistics Agent

Objective:

> Minimize delivery and transportation risk.

Considers:

```text
lead time
warehouse capacity
transport constraints
distribution distance
delivery windows
```

---

## 10.5 Supplier Agent

Objective:

> Select reliable suppliers.

Considers:

```text
supplier availability
historical fulfillment
current lead time
pricing
reliability
```

---

## 10.6 Waste Agent

Objective:

> Minimize expected unsold or spoiled inventory.

Considers:

```text
forecast uncertainty
shelf life
inventory levels
consumption rate
```

---

# 11. MCP Integration

MCP exists on both sides of Civitas and the two boundaries must remain distinct.

## 11.1 Inbound product interface

Codex invokes a small set of Civitas workflow tools:

```text
plan_procurement_goal()
get_planning_run()
get_decision_summary()
prepare_execution()
approve_execution()
execute_approved_plan()
get_execution_audit()
```

These tools accept and return strict application-owned contracts. They delegate to the same guarded application services as any optional UI and do not expose repositories, arbitrary SQL, low-level Parliament transitions, or direct provider writes.

## 11.2 Outbound operational interface

A participating partner's MCP server becomes the system's **real operational environment**.

The agents should use MCP tools to retrieve and possibly update real/simulated business state.

Possible tools include:

```text
get_inventory()
get_demand()
get_supplier_data()
get_supplier_price()
get_lead_time()
get_warehouse_capacity()
get_order_history()
get_product_details()
create_procurement_order()
update_inventory()
```

The exact tools depend on the participating partner.

The architecture must treat MCP responses as first-class evidence.

Example:

```text
MCP Call
   ↓
Tool response
   ↓
Evidence ID
   ↓
Agent claim
   ↓
Parliament argument
   ↓
Decision
```

This allows Agent Jury to trace where a claim came from.

Codex must not receive these outbound operational tools directly. Dissent receives only independently namespaced read tools. The guarded execution service receives only the write tools needed for an approved action.

## 11.3 Conversation versus authority

Codex may interpret a user's goal and summarize Civitas responses. It cannot make an LLM-generated quantity executable or treat a bare conversational confirmation as sufficient approval. Execution requires a short-lived challenge bound to the immutable plan hash, organization, operator, approved totals, and policy version, followed by freshness revalidation and an idempotent write.

---

# 12. Evidence Model

Every important agent statement should be represented as a structured claim.

Example:

```json
{
  "claim_id": "C17",
  "agent_id": "supplier_agent",
  "claim": "Supplier A can deliver 500 units within 3 days",
  "evidence_ids": ["E31"],
  "source_ids": ["MCP_SUPPLIER_14"],
  "derived_from": [],
  "confidence": 0.91,
  "timestamp": "..."
}
```

An agent-derived claim:

```json
{
  "claim_id": "C21",
  "agent_id": "logistics_agent",
  "claim": "Supplier A is the fastest option",
  "evidence_ids": ["C17", "E35"],
  "derived_from": ["C17"],
  "confidence": 0.83
}
```

The distinction between:

```text
external evidence
```

and:

```text
another agent's claim
```

must be preserved.

---

# 13. Evidence Graph

Represent decisions as a graph.

## Node Types

```text
Agent
Claim
Evidence
MCP Tool Call
Source
Proposal
Decision
```

## Edge Types

```text
PRODUCED
SUPPORTS
DERIVED_FROM
RETRIEVED_FROM
CONTRADICTS
DEPENDS_ON
USED_IN
```

Example:

```text
               MCP Supplier Tool
                       │
                   RESPONSE
                       │
                       ▼
                  Evidence E1
                       │
                   SUPPORTS
                       │
                       ▼
                    Claim C1
                       │
                  PRODUCED_BY
                       │
                       ▼
                Supplier Agent
                       │
                  ARGUES_FOR
                       │
                       ▼
                  Proposal P1
```

---

# 14. Agent Parliament Protocol

Parliament operates through rounds.

## Round 1 — Independent Proposals

Each agent receives the current environment and generates its preferred plan.

Example:

```text
Demand:
Buy 500 units.

Cost:
Buy 300 units from Supplier A.

Freshness:
Buy maximum 350.

Logistics:
Buy 250 locally and redistribute 100.

Supplier:
Use Supplier A.
```

---

## Round 2 — Challenge

Agents see competing proposals.

Each agent identifies:

```text
proposal weakness
constraint violation
objective tradeoff
missing evidence
```

Example:

```text
Freshness Agent:
500 units will exceed expected 7-day consumption.

Cost Agent:
Reducing to 300 increases shortage risk.

Logistics Agent:
Supplier A's lead time makes the plan infeasible.
```

---

## Round 3 — Negotiation

Agents modify their proposals based on evidence and concessions.

Example:

```text
Initial:
500 units

Concession:
350 units

Final proposal:
350 units from Supplier A
+
100 units redistributed internally
```

Each change should contain a reason.

Example:

```json
{
  "change": "quantity 500 -> 350",
  "reason": "freshness constraint",
  "evidence": ["E17"],
  "agent": "freshness_agent"
}
```

---

# 15. Parliament Output

The Parliament produces:

```json
{
  "proposal_id": "P4",
  "procurement": [
    {
      "supplier": "A",
      "quantity": 250
    },
    {
      "supplier": "B",
      "quantity": 100
    }
  ],
  "allocation": {
    "warehouse_1": 200,
    "warehouse_2": 150
  },
  "rationale": [
    "satisfies expected demand",
    "within warehouse capacity",
    "reduces expected spoilage"
  ],
  "supporting_claims": [
    "C4",
    "C8",
    "C11"
  ]
}
```

---

# 16. Agent Jury

Jury receives:

```text
candidate proposal
+
agent claims
+
evidence graph
+
source metadata
+
agent dependency information
```

It performs four major checks.

---

# 17. Jury Function 1 — Evidence Provenance

For every major claim, determine:

```text
Where did this information originate?
```

Example:

```text
Claim:
Supplier A delivers in 3 days.

Origin:
MCP Supplier API

Derived claims:
C17
C22
C28
```

This prevents downstream agents from appearing to provide independent evidence when they simply inherited the same claim.

---

# 18. Jury Function 2 — Independence Analysis

Determine whether apparently independent agents are actually dependent on the same evidence.

Example:

```text
Agent A → Source X
Agent B → Source X
Agent C → Agent A
Agent D → Source Y
```

Naive consensus:

```text
4 / 4
```

Jury interpretation:

```text
Independent source groups:
X
Y

Effective independent support:
2
```

The system should distinguish:

```text
agent diversity
```

from:

```text
evidence diversity
```

These are not the same.

---

# 19. Jury Function 3 — Dissent Agent

The Dissent Agent has a different objective from the Parliament.

Its instruction is:

> **Assume the current procurement proposal is wrong. Find evidence that would invalidate it.**

It searches for:

```text
contradictory data
stale information
missing constraints
alternative suppliers
unexpected demand
supplier unreliability
capacity conflicts
```

Example:

```text
Parliament:
Supplier A can deliver in 3 days.

Dissent Agent:
Current supplier record indicates 10-day lead time.
```

Result:

```text
CONTRADICTION FOUND
```

---

# 20. Jury Function 4 — Decision Integrity

Calculate an interpretable score from observable properties.

Example factors:

```text
Consensus strength
Evidence independence
Source diversity
Provenance completeness
Contradictions
Dissent findings
```

Example:

```text
Consensus:              5 / 6
Independent evidence:   2
Shared dependencies:    HIGH
Contradictions:         1
Dissent:                FOUND

Decision Integrity:
41 / 100

Recommendation:
INVESTIGATE
```

The score must always be accompanied by the reasons behind it.

---

# 21. Decision States

The Jury can return:

## APPROVE

Evidence is sufficiently strong and no significant unresolved contradiction exists.

```text
Integrity >= threshold
```

The planner can proceed to execution.

---

## INVESTIGATE

Evidence is insufficient or excessively correlated.

```text
Integrity below threshold
```

The planner generates another investigation step.

---

## ESCALATE

The decision is high-impact and significant contradictions remain unresolved.

Human approval may be required.

---

## REJECT

Evidence strongly contradicts the proposed plan or required constraints cannot be satisfied.

---

# 22. Adaptive Replanning

This is a key part of the system.

Jury must not merely reject proposals.

It should provide structured feedback to the planner.

Example:

```json
{
  "status": "INVESTIGATE",
  "reasons": [
    "supplier lead time has conflicting values",
    "three agents rely on the same source"
  ],
  "recommended_investigation": [
    "verify current supplier lead time"
  ]
}
```

The planner converts this into a new task:

```text
1. Verify Supplier A lead time
2. Recalculate feasible procurement
3. Reopen Parliament
```

Then:

```text
Parliament
 ↓
new proposal
 ↓
Jury
 ↓
APPROVE
```

---

# 23. The Most Important Demo Failure

The primary demonstration should involve a **false consensus**.

Suppose:

```text
Supplier A:
cheap
3-day lead time
500 units
```

Five agents recommend Supplier A.

Naive architecture:

```text
5 / 5 agents agree
        ↓
BUY
```

Agent Jury discovers:

```text
Agent A → supplier record
Agent B → same supplier record
Agent C → Agent A
Agent D → same supplier record
Agent E → Agent B
```

Therefore:

```text
Consensus: 5 / 5

Independent evidence:
1

Shared dependency:
VERY HIGH
```

---

# 24. Dissent Twist

Dissent Agent investigates Supplier A.

It discovers a current record:

```text
Supplier A:
lead time = 10 days
```

The original 3-day information was stale.

Now:

```text
Consensus:
5 / 5

Actual evidence independence:
LOW

Contradiction:
FOUND

Integrity:
38 / 100
```

The plan is rejected.

---

# 25. Replanning

Planner generates:

```text
New task:
Find feasible combination satisfying demand
without Supplier A's assumed 3-day lead time.
```

Parliament debates again.

Potential new outcome:

```text
Supplier B:
250

Supplier C:
100

Internal redistribution:
50
```

Jury evaluates again:

```text
Independent evidence:
HIGH

Contradictions:
NONE

Integrity:
89 / 100
```

Then:

```text
APPROVE
```

---

# 26. Real Action

The system then performs an actual action through MCP.

For example:

```text
create_procurement_order()
```

or:

```text
update_inventory_allocation()
```

or:

```text
create_purchase_plan()
```

The final system should not stop at generating a recommendation.

The decision should result in a tool call that changes the simulated operational environment.

This satisfies the **Beyond Chat** requirement.

---

# 27. Full End-to-End Flow

```text
USER

"Procure enough inventory for the next 7 days while
minimizing cost and avoiding spoilage."

        ↓

PLANNER

        ↓

Retrieve:
- demand
- inventory
- suppliers
- prices
- lead times
- shelf life
- warehouse capacity

        ↓

MCP

        ↓

SPECIALIZED AGENTS

Demand
Cost
Freshness
Logistics
Supplier
Waste

        ↓

PARLIAMENT

Independent proposals
        ↓
Challenges
        ↓
Negotiation
        ↓
Candidate procurement plan

        ↓

AGENT JURY

Evidence graph
        ↓
Provenance
        ↓
Independence
        ↓
Dissent
        ↓
Decision Integrity

        ↓

      ┌──────────────┐
      │              │
   APPROVE       INVESTIGATE
      │              │
      ▼              ▼
   EXECUTE        REPLAN
                     │
                     ▼
                 PARLIAMENT

```

---

# 28. Primary Interaction and Optional Audit UI

The primary interaction is a Codex conversation, not a dashboard. The operator states a goal, receives material progress updates, reviews the exact decision summary and business impact, and explicitly approves a short-lived execution challenge.

```text
Operator:
Protect seven days of demand while minimizing cost and food waste.

Codex:
Five roles preferred Supplier A, but their support collapses to one stale
source. Dissent found a current 10-day lead time. Civitas blocked that plan,
replanned with Supplier B, and now has Integrity 92/100 with all gates passed.
Approve the exact revised plan?
```

The React UI is an optional, read-only deep audit view. It should focus on **the decision process**, not a generic enterprise dashboard, and must never provide an alternative execution path.

## Audit View 1 — Procurement Request

```text
7-Day Procurement Goal

Demand:
12,000 units

Current inventory:
7,800 units

Projected shortage:
4,200 units
```

---

## Audit View 2 — Parliament

Display agents as participants.

```text
┌─────────────────────────────────────────────┐
│              AGENT PARLIAMENT               │
│                                             │
│ Demand       BUY 500                        │
│ Cost         BUY 300                        │
│ Freshness    BUY 350                        │
│ Logistics    BUY 250 + redistribution       │
│ Supplier     USE SUPPLIER A                 │
│ Waste        BUY 320                        │
│                                             │
│ Negotiation Round: 2                        │
└─────────────────────────────────────────────┘
```

Show proposals changing in real time.

---

# 29. Jury View

After Parliament produces a proposal:

```text
┌─────────────────────────────────────────────┐
│                 AGENT JURY                  │
│                                             │
│ Proposal: 350 units                         │
│                                             │
│ Agent Consensus       5 / 6                 │
│ Independent Evidence  2 / 5       ⚠         │
│ Shared Sources        HIGH        ⚠         │
│ Contradictions        1           ⚠         │
│ Dissent                FOUND       ⚠        │
│                                             │
│ DECISION INTEGRITY      41 / 100            │
│                                             │
│ STATUS: INVESTIGATE                         │
└─────────────────────────────────────────────┘
```

---

# 30. Evidence Graph View

Show:

```text
                 Source S1
                /    |    \
               /     |     \
              ▼      ▼      ▼
         Agent A  Agent B  Agent C
              \      |      /
               \     |     /
                ▼    ▼    ▼
                CONSENSUS
                    │
                    ▼
                 Proposal
```

The visual should make shared evidence immediately obvious.

---

# 31. Key Product Metrics

The MVP should measure:

## Consensus Accuracy

How often does Parliament reach the correct decision?

## False Consensus Detection

How often does Jury correctly identify consensus created by shared evidence?

## Independence Detection

How accurately does the system identify common evidence dependencies?

## Contradiction Detection

How often does Dissent identify meaningful contradictory evidence?

## Decision Integrity Accuracy

How well does the integrity classification correspond to ground truth?

## Replanning Success

How often does Jury feedback lead to a better procurement decision?

---

# 32. Evaluation Dataset

Create synthetic but realistic scenarios.

Each scenario should contain:

```text
products
warehouses
demand
suppliers
prices
lead times
shelf life
transport constraints
```

Create different difficulty levels.

---

# 33. Evaluation Scenario A — Genuine Consensus

All agents receive genuinely independent evidence.

Expected:

```text
High independence
High integrity
APPROVE
```

---

# 34. Evaluation Scenario B — Shared Evidence

All agents rely on the same supplier record.

Expected:

```text
High apparent consensus
Low independence
INVESTIGATE
```

---

# 35. Evaluation Scenario C — Agent Echo

```text
Agent A → claim
Agent B → copies A
Agent C → copies B
Agent D → copies C
```

Expected:

```text
Very low independent support
```

---

# 36. Evaluation Scenario D — Stale Information

Old supplier lead-time information conflicts with current information.

Expected:

```text
Contradiction detected
Dissent successful
REPLAN
```

---

# 37. Evaluation Scenario E — HalluSquatting-Inspired Information Propagation

Simulate a resolver agent producing an incorrect resource/entity identifier.

Downstream agents use information derived from that identifier.

Expected:

```text
No genuine independent evidence
```

The demonstration should be completely sandboxed and benign.

The HalluSquatting research itself used controlled, responsible-disclosure experiments and explicitly discusses the dual-use nature of the technique.

---

# 38. Evaluation Scenario F — Genuine Disagreement

Agents disagree because objectives genuinely conflict.

Example:

```text
Cost:
Supplier A

Freshness:
Supplier B

Logistics:
Supplier C
```

Expected:

```text
Parliament negotiates
Jury does not falsely interpret disagreement as failure
```

The purpose of the Jury is not to eliminate disagreement.

It is to determine whether the final decision is **well-supported**.

---

# 39. Stretch Goal — Temporal Evidence

Add freshness to every evidence item.

Example:

```json
{
  "source": "supplier_api",
  "value": "3 day lead time",
  "observed_at": "...",
  "volatility": "high"
}
```

The Jury can flag:

```text
Evidence age:
14 days

Expected volatility:
HIGH

→ verify before execution
```

This is especially relevant to food procurement because demand, inventory, prices, lead times and perishability change continuously.

---

# 40. Stretch Goal — Information-Gain Investigation

When Jury finds uncertainty, the planner can ask:

> What is the most useful next investigation?

For hypotheses:

```text
H1:
Supplier shortage

H2:
Demand spike

H3:
Logistics delay
```

select the tool call most likely to distinguish them.

Example:

```text
Check current supplier capacity

Expected information gain:
HIGH

Cost:
LOW

→ execute investigation
```

---

# 41. Stretch Goal — Contamination Tracking

Track the spread of a questionable claim.

```text
Source S1
   ↓
Agent A
   ↓
Claim C1
   ↓
Memory
   ↓
Agent B
   ↓
Claim C2
   ↓
Agent C
```

Calculate:

```text
downstream agents affected
downstream decisions affected
actions affected
```

---

# 42. Stretch Goal — Multi-Round Parliament

Instead of a single negotiation:

```text
Round 1
 ↓
Round 2
 ↓
Round 3
 ↓
Jury
```

allow the Jury's evidence findings to re-enter Parliament.

This creates a feedback loop:

```text
PARLIAMENT
     ↓
JURY
     ↓
NEW INFORMATION
     ↓
PARLIAMENT
     ↓
JURY
```

---

# 43. Stretch Goal — Human Approval

For high-value procurement:

```text
Integrity:
72

Amount:
₹8,00,000

Risk:
HIGH

→ Human approval required
```

For smaller purchases:

```text
Integrity:
91

Amount:
₹12,000

Risk:
LOW

→ Autonomous execution
```

This makes autonomy proportional to decision risk.

---

# 44. Integration Strategy

The system should be modular.

```text
agent-jury/
│
├── planner/
│   └── planner.py
│
├── parliament/
│   ├── parliament.py
│   ├── negotiation.py
│   ├── demand_agent.py
│   ├── cost_agent.py
│   ├── freshness_agent.py
│   ├── logistics_agent.py
│   ├── supplier_agent.py
│   └── waste_agent.py
│
├── jury/
│   ├── jury.py
│   ├── evidence_graph.py
│   ├── provenance.py
│   ├── independence.py
│   ├── dissent.py
│   └── integrity.py
│
├── mcp/
│   ├── inbound_server.py
│   ├── outbound_client.py
│   └── contracts.py
│
├── environment/
│   ├── inventory.py
│   ├── suppliers.py
│   ├── demand.py
│   └── warehouses.py
│
├── evaluation/
│   ├── scenarios.py
│   ├── metrics.py
│   └── tests.py
│
├── audit_viewer/
│
├── app.py
├── README.md
└── PLAN.md
```

---

# 45. Implementation Priority

The Codex/MCP product work is divided into merge-safe branches with owned paths and acceptance tests in [MCP_AGENT_WORKPLAN.md](MCP_AGENT_WORKPLAN.md). That work plan is authoritative for parallel ownership and merge order.

## Phase 1 — Core Environment

Build:

```text
[ ] Inventory
[ ] Demand
[ ] Suppliers
[ ] Prices
[ ] Lead times
[ ] Warehouse capacity
[ ] Shelf life
```

Use deterministic data initially.

---

## Phase 2 — MCP Integration

Implement:

```text
[ ] Inbound Civitas MCP server
[ ] Intent-level Codex tool contracts
[ ] Local STDIO and deployed Streamable HTTP transports
[ ] Organization and operator authentication
[ ] Outbound MCP connection
[ ] Operational read tools
[ ] Structured tool responses
[ ] Tool result → evidence conversion
[ ] At least one write/action tool
```

---

## Phase 3 — Parliament

Implement:

```text
[ ] Planner
[ ] Demand Agent
[ ] Cost Agent
[ ] Freshness Agent
[ ] Logistics Agent
[ ] Supplier Agent
[ ] Waste Agent
[ ] Proposal format
[ ] Negotiation rounds
```

Start with 4 agents if time becomes limited.

---

## Phase 4 — Evidence System

Implement:

```text
[ ] Claim schema
[ ] Evidence schema
[ ] Source schema
[ ] Evidence graph
[ ] Provenance tracking
```

This should be deterministic wherever possible.

---

## Phase 5 — Jury

Implement:

```text
[ ] Independence analyzer
[ ] Dissent agent
[ ] Contradiction detection
[ ] Integrity score
[ ] Decision state
```

---

## Phase 6 — Planner/Jury Feedback Loop

Implement:

```text
[ ] Jury → planner feedback
[ ] Investigation tasks
[ ] Replanning
[ ] Parliament rerun
[ ] Final approval
```

---

## Phase 7 — Execution

Implement:

```text
[ ] Procurement order/action
[ ] Inventory update
[ ] Final audit trail
```

---

## Phase 8 — Optional Audit Viewer

Only after the complete pipeline works.

Priority:

```text
1. Parliament
2. Jury
3. Evidence graph
4. Final decision
```

---

# 46. Minimum Viable Product

If time becomes limited, the MVP is:

```text
1. Inbound Codex-compatible Civitas MCP server
2. Planner
3. 4 specialized agents
4. Outbound operational MCP integration
5. Parliament negotiation
6. Evidence graph
7. Independence analysis
8. Dissent agent
9. Decision Integrity Score
10. Jury → Replan loop
11. Explicit guarded approval
12. Real idempotent MCP action
```

Everything else can be added later.

---

# 47. What NOT to Build First

Do not spend hackathon time on:

```text
[ ] Large enterprise authentication system
[ ] Complex distributed infrastructure
[ ] Perfect optimization solver
[ ] Full production database architecture
[ ] Supporting every MCP server
[ ] Supporting every agent framework
[ ] Complex mathematical independence modeling
[ ] Massive UI
```

The demo should prove the conceptual innovation first.

---

# 48. The Critical Differentiation

This project is **not**:

### A generic procurement agent

The official problem already asks for this.

### A generic multi-agent system

The agents have deliberately conflicting objectives and negotiate.

### An LLM-as-a-Judge system

A normal LLM judge evaluates an output.

Agent Jury evaluates:

```text
claims
+
evidence
+
provenance
+
dependencies
+
independence
+
contradictions
```

### A security firewall

Security is only one possible application.

The underlying problem is:

> **collective decision integrity in autonomous multi-agent systems.**

---

# 49. Product Vision

Long-term, this can become middleware for autonomous enterprise systems.

```text
Existing Agent System
        │
        ▼
  Agent Parliament
        │
        ▼
     Agent Jury
        │
        ▼
Decision Integrity
        │
        ▼
Approved Action
```

Potential future applications:

```text
Food procurement
Industrial supply chain
Insurance
Financial services
Procurement
Customer intelligence
Operations
Research
Strategic decision-making
```

The underlying architecture remains the same.

---

# 50. Why This Could Be a Product

The procurement application is only the initial vertical.

The reusable product layer is:

```text
Evidence-aware
multi-agent
decision orchestration
```

Organizations could connect existing agents to Agent Jury and gain:

```text
decision provenance
evidence lineage
independence analysis
dissent
decision integrity
adaptive investigation
```

without replacing their existing agents.

---

# 51. Final User Experience

The user should be able to say:

> **“Procure enough food for the next seven days, minimize cost and waste, and distribute it across our warehouses.”**

Codex sends the goal to Civitas through MCP. The system independently plans and investigates.

Agents disagree.

Parliament negotiates.

Jury challenges the proposal.

The system discovers weak evidence.

Planner replans.

Parliament negotiates again.

Jury approves.

The system executes the procurement action.

Codex presents a compact decision and approval request. The optional viewer can show the complete evidence graph:

```text
┌──────────────────────────────────────────────┐
│              PROCUREMENT COMPLETE            │
│                                              │
│ Purchase: 350 units                          │
│ Suppliers: A + B                             │
│ Warehouses: W1 + W2                          │
│                                              │
│ Parliament consensus: 5 / 6                  │
│ Independent evidence: HIGH                   │
│ Contradictions: 0                            │
│ Dissent: RESOLVED                             │
│                                              │
│ Decision Integrity: 91 / 100                 │
│                                              │
│ ✓ MCP ACTION EXECUTED                        │
└──────────────────────────────────────────────┘
```

---

# 52. Hackathon Demo Narrative

The demo should revolve around one sentence:

> **“Six agents agreed. We almost executed. Then the Jury discovered that they were all agreeing with the same bad information.”**

Sequence:

```text
1. User gives Codex a procurement goal.

2. Codex invokes the Civitas MCP server.

3. Planner decomposes the task.

4. Civitas queries operational MCP providers.

5. Agents produce conflicting proposals.

6. Parliament negotiates and reaches consensus.

7. Jury reconstructs evidence.

8. Jury discovers shared evidence.

9. Dissent discovers contradictory information.

10. Jury rejects consensus.

11. Planner automatically replans.

12. Parliament negotiates again.

13. Jury approves.

14. Codex presents the immutable plan and asks for explicit approval.

15. Civitas revalidates and executes procurement idempotently through MCP.

16. Codex returns the receipt; the optional audit view explains the lineage.
```

---

# 53. Final Positioning

The official challenge asks:

> **Can an autonomous agent optimize food procurement and distribution?**

Our system asks a harder question:

> **Can a society of autonomous agents negotiate a procurement decision without becoming confidently wrong together?**

And solves it through:

```text
AGENT PARLIAMENT
→ resolves conflicting objectives

AGENT JURY
→ evaluates evidence behind agreement

PLANNER
→ responds to uncertainty

MCP
→ lets Codex invoke Civitas and connects Civitas to the real operational environment

EXECUTION
→ turns the decision into action
```

---

# 54. One-Line Product Description

> **Civitas is a Codex-connected procurement agent where specialized agents negotiate solver-backed plans and a Jury verifies the evidence behind their agreement before any real-world action is allowed.**

---

# 55. Core Tagline

> **Don't just make agents agree. Make sure their agreement deserves to be trusted.**
