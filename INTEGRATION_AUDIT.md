# End-to-end integration audit

Audit date: 2026-08-27  
Branch: `integration/end-to-end`

## Executive result

The deterministic false-consensus demonstration works end to end with simulated providers. The repository is not yet a production deployment. The approved product direction now makes a Codex-compatible inbound MCP server the primary interface and demotes the React application to an optional audit viewer; that inbound facade is documented but not implemented at this audit revision. The complete test suite, strict Python type checking, Python lint/format checks, frontend lint/type checking, production frontend build, Alembic upgrade, and dependency advisory scans pass.

No request-controlled raw SQL or SQL string interpolation was found. SQLAlchemy expressions use bound parameters. No committed private key or provider credential was found. `pip-audit` and `pnpm audit --prod --audit-level high` reported no known vulnerable installed dependencies at audit time.

## Delivery matrix

| Agent | Status | Audit result |
| --- | --- | --- |
| 0 — Foundation | Complete | uv/pnpm workspaces, strict contracts, ports, Docker PostgreSQL, deterministic clock/ID ports, and CI exist. |
| 1 — Persistence | Complete for MVP | One Alembic head builds from empty PostgreSQL; ORM, repositories, evidence/audit records, FEFO ledger, reconciliation, uniqueness, and concurrent reservation tests exist. Generic repositories remain unsafe as direct multi-tenant API surfaces. |
| 2 — Optimization | Complete for golden scope | Integer translation, two-stage solving, alternatives, scorecards, minimax regret, verifier, exhaustive oracle, and property tests exist. |
| 3 — Evidence/Jury | Complete for policy v1 | Typed claims, source grouping, graph projection, contradictions, deterministic Integrity v1, hard gates, reason codes, and ordered Dissent protocol are tested. |
| 4 — Integrations | Complete for offline contracts | Provider-neutral Groq and MCP adapters, strict output validation, retries/timeouts, clean-room read-only policy, evidence conversion, fake adapters, and idempotent mock writes exist. |
| 5 — Parliament/workflow | Partial production composition | Six deterministic roles, rounds, solver selection, Jury routing, bounds, events, and durable event snapshots exist. The generic workflow still needs a production investigation worker and optional model-backed explanation composition. |
| 6 — Evaluation | Complete | Ten deterministic scenarios, hidden/visible separation, interventions, lineage expectations, independent verification, Hypothesis generation, metrics, and reports exist. |
| 7 — Audit viewer | Complete for demo | The optional Vite/React viewer provides the goal, Parliament story, alternatives, evidence graph, separate Integrity components and gates, replanning, execution status, typed SSE, and mock playback. |
| 8 — API/execution | Complete at tested boundary | Authenticated organization-scoped guarded routes, cursor/`Last-Event-ID`, freshness checks, plan/Jury binding, FEFO reservations, idempotency locking, MCP writes, and compensation states are integration-tested. Deployment composition is environment-specific. |
| 9 — End-to-end | Complete for simulated demo | The UI and demo API exercise evidence retrieval → Parliament → solver alternatives → Jury/Dissent → replanning → approval → freshness checks → one idempotent mock MCP write. |

## Security and correctness defects fixed

- Added constant-time bearer-token authentication and organization ownership checks to every guarded API route.
- Blocked cross-run and cross-organization plan, Jury, supplier, SKU, warehouse, and lot references before external writes.
- Scoped duplicate lookups by organization and serialized concurrent retries using a PostgreSQL advisory transaction lock.
- Required selected, feasible, approved plans; the matching Integrity policy; approval score; all nine hard gates; and recorded Dissent robustness.
- Required non-empty idempotency keys at the MCP write boundary.
- Bounded model responses to 1 MiB and expanded structured-output enforcement beyond basic JSON types.
- Made malformed MCP refresh values fail closed rather than flow into execution.
- Removed the model-adapter test hang caused by default-executor threads attached to short-lived pytest event loops.
- Replaced predictable demo run IDs, bounded retained demo runs, made demo execution asynchronous, and closed an SSE replay/subscription race.
- Fixed the frontend’s named-SSE handling; previously native live events were not added to the UI timeline.
- Restored separate Integrity-component and hard-gate rendering required by the frontend acceptance criteria.
- Corrected strict typing and lint failures that would have failed the checked-in CI workflow.
- Updated stale setup instructions from backend port 8000 to 8001 and documented demo versus guarded trust boundaries.

## Remaining work before production

1. Implement the intent-level inbound Civitas MCP facade described in `MCP_INTERFACE.md`, reusing guarded application services rather than creating a second execution path.
2. Add local STDIO and deployed Streamable HTTP composition, with organization/operator authentication and short-lived plan-hash approval challenges.
3. Compose the guarded API factory, database, provider credentials, policy identity, and operator identity in a deployment entry point. The default app remains the offline demo.
4. Implement the generic investigation step as a durable read-only outbound MCP worker instead of relying on a callback or demo-specific orchestration.
5. Decide whether Parliament explanations should use the model adapter. Solver ownership and deterministic scorecards must remain authoritative.
6. Replace generic unscoped repository access with organization-required repository methods before exposing those repositories to additional APIs, MCP tools, or workers.
7. If LangGraph-native persistence is required, replace the topology-only compiled graph with a real checkpointer; current resume durability comes from PostgreSQL event snapshots.
8. Add deployment controls: TLS termination, OAuth or secret-manager integration, credential rotation, operator roles, rate limiting, structured security logs, backups, and provider compensation implementations.

The branch history does not match the proposed one-branch-per-agent merge narrative. Git contains dedicated foundation and persistence commits, while optimization/evidence branch pointers remained at the foundation commit and the remaining features arrived together in `b262aed`. This is traceability debt, not a runtime defect; the audit assessed the integrated tree rather than inferring completion from branch names.
