# Civitas — Tech Stack

| Layer               | Technology               |
| ------------------- | ------------------------ |
| Language            | Python 3.12+             |
| Agent Orchestration | LangGraph                |
| LLM Provider        | Groq API                 |
| LLM Model           | `openai/gpt-oss-120b`    |
| LLM Integration     | Provider-neutral adapter (Groq implementation) |
| Structured Outputs  | Pydantic                 |
| MCP                 | Python MCP SDK           |
| Backend             | FastAPI                  |
| Database            | PostgreSQL (system of record) |
| Database Access     | SQLAlchemy 2.x async + psycopg 3 |
| Migrations          | Alembic                  |
| Evidence Graph      | PostgreSQL lineage + NetworkX projection |
| Optimization        | Google OR-Tools          |
| Frontend            | React + Vite             |
| Frontend Language   | TypeScript               |
| Styling             | Tailwind CSS             |
| Graph Visualization | React Flow               |
| Live Updates        | Server-Sent Events (SSE) |
| Python Testing      | pytest + Hypothesis + pytest-asyncio |
| Python Packages     | uv                       |
| Frontend Packages   | pnpm                     |
| Containerization    | Docker                   |

## Architectural Boundaries

- OR-Tools constructs and validates feasible procurement plans. LLM agents investigate, propose objectives and trade-offs, challenge evidence, and compare solver-generated alternatives; they do not directly authorize quantities for execution.
- PostgreSQL is the durable source of truth for evidence, claims, lineage, workflow state, decisions, and execution audit records. NetworkX is a rebuildable in-memory projection used for graph analysis. React Flow is a read-only visualization projection.
- All model calls go through an application-owned provider adapter. Groq and `openai/gpt-oss-120b` are the initial implementation, not domain-level dependencies.
- MCP write operations require a final revalidation gate, policy-based approval, an idempotency key, and an immutable audit record.
- React + Vite is preferred for the MVP because the product is an interactive client application and does not currently require server-side rendering.
- Domain data uses normalized, versioned relational tables. Planning runs own timezone-aware, UTC-bounded daily buckets for the MVP; the schema remains capable of other bucket durations. JSONB is limited to raw payloads and optional metadata.
- Perishable inventory is lot-based. PostgreSQL stores an append-only inventory-movement ledger, with a transactionally maintained balance projection where useful; allocation follows FEFO and preserves `use_by` versus `best_before` semantics.
- OR-Tools uses two-stage optimization: first minimize priority- and urgency-weighted shortage, then hold fulfillment constant and generate Pareto alternatives across cost, waste, risk, redistribution, holding, and concentration. All feasibility and safety constraints are deterministic.
- Parliament uses versioned deterministic role scorecards and minimax-regret selection over solver alternatives. LLM agents explain and challenge; they neither invent utility scores nor directly set solver weights.
- Decision Integrity v1 is deterministic and versioned: coverage 20%, independence 20%, provenance 15%, freshness 15%, source diversity 10%, contradiction resolution 10%, and dissent robustness 10%. Hard gates override the score; consensus is displayed with zero weight.
- Evaluation uses versioned golden scenarios, generated invariants, an independent constraint verifier, and exhaustive enumeration for small oracle cases.
- Dissent runs in a clean-room model and cache context with independent read-only tool access before comparing its fresh evidence with Parliament lineage.
- Execution Freshness v1 applies predicate-specific TTLs, refreshes mutable inputs at the write boundary, reruns feasibility and Integrity, and fails closed when the exact approved action cannot proceed.
- SQLAlchemy sessions are unit-of-work scoped, PostgreSQL enforces ledger and idempotency integrity, and Alembic—not application startup—owns schema creation and evolution.
- Required CI uses deterministic fakes, property tests, golden scenarios, and an ephemeral migrated PostgreSQL database; live provider tests are opt-in.
- uv and pnpm lockfiles are authoritative and frozen in CI and container builds.
