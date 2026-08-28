# Agent 6 handoff — live planning investigation

Branch: `live/planning-investigation`

Base: local `main` at `b0052bc`, which already integrates Agents 1–5.

## Delivered

- Added `JuryDirectedInvestigator`, which converts structured Jury feedback into bounded,
  read-only operational MCP calls, persists typed claims/evidence/call lineage, and creates a new
  immutable `input_data_version` for the next OR-Tools run.
- Added `DurableCleanRoomJury`, which records its clean-room plan before retrieval, uses the
  credential-isolated Dissent evidence client, records retrieval and comparison phases, and runs
  deterministic Decision Integrity v1 gates.
- Added `PostgreSQLEvidenceLedger` for claims, evidence, MCP calls, source identity, claim links,
  lineage, and Dissent audit phases. Identical observations deduplicate within a planning run but
  remain independently reproducible across planning runs.
- Extended resumable checkpoints with evidence fingerprints, canonical source groups, completed
  and unavailable investigation tasks, and investigation tool/cost accounting.
- Added deterministic repeated-evidence detection and fail-closed escalation for unavailable
  required checks, cycle/tool/cost/deadline bounds, and empty solver alternative sets.
- Connected Agent 3 provider connections to the Agent 1 runtime through
  `ProviderPlanningRuntime.from_connections(...)`. This binds the exact read-only Dissent client
  and its real clean-room namespace; no provider write client enters planning or Jury code.
- Solver authority is preserved: fresh complete supplier-offer records may replace solver input
  facts, then OR-Tools regenerates alternatives. Agents and Dissent never emit procurement
  quantities.

## Migration

New Alembic head: `a61d9c3e7f20`.

It adds `dissent_investigations` and scopes evidence identity uniqueness by planning run. Apply
with:

```bash
uv run alembic upgrade head
```

## Composition

After Agent 3 onboards a provider and returns `ProviderConnections`:

```python
planning = ProviderPlanningRuntime.from_connections(connections)
runtime = build_runtime(settings, provider_planning=planning)
worker = build_worker(settings, provider_planning=planning)
```

If `provider_planning` is absent, the existing fail-closed Jury remains active. The environment-only
`create_worker()` intentionally stays fail-closed until Agent 7 supplies a production provider
registration/credential factory; it does not fabricate a provider or weaken credential isolation.

## Validation

- `ruff check` and `ruff format`: clean
- `mypy src/civitas`: clean (98 source files)
- complete suite on an empty, migrated PostgreSQL 17 stack: `161 passed`
- focused post-composition checks: `25 passed`

The shared port-55432 database contained abandoned rows from concurrent agent test runs and caused
two queue tests to claim the wrong run. Re-running the complete suite against an isolated empty
database on port 55434 passed. No production/shared data was removed.

## Follow-up boundaries

- Agent 7 should construct Agent 3 `ProviderConnections` from secret-backed registration at server
  and worker startup, then pass `ProviderPlanningRuntime.from_connections(...)` to both composition
  roots.
- Agent 9 should include a provider payload whose refreshed complete `offers` set changes solver
  alternatives, plus a duplicate canonical-source response to assert bounded escalation.
- Strong invalidity remains reserved for the independent deterministic verifier. A fresh Dissent
  contradiction correctly routes to `INVESTIGATE`; it does not automatically become `REJECT`.
