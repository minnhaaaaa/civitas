# Civitas

Civitas is an autonomous multi-agent food-procurement system that combines negotiation, evidence-lineage analysis, adaptive replanning, deterministic optimization, and guarded MCP execution. Its target product interface is a Codex-compatible MCP server: operators state a procurement goal conversationally, Codex invokes Civitas's intent-level tools, and Civitas owns the complete planning and safety workflow.

Specialized Parliament agents investigate competing objectives and compare solver-generated procurement alternatives. An evidence-aware Jury then evaluates provenance, genuine source independence, contradictions, and adversarial dissent before an action can pass the execution safety boundary.

The React application is an optional, read-only evidence and execution-audit viewer rather than the product's primary entry point. The repository includes strict inbound MCP contracts, a validated MCP composition entry point, the transport-neutral procurement facade, a deterministic integration suite, and the viewer-based false-consensus demonstration. Durable workflow, provider, guarded-execution, and production identity adapters remain separate bounded workstreams. See [MCP_INTERFACE.md](MCP_INTERFACE.md), [MCP_AGENT_WORKPLAN.md](MCP_AGENT_WORKPLAN.md), [PLAN.md](PLAN.md), [AGENTS.md](AGENTS.md), [TECH_STACK.md](TECH_STACK.md), and [SECURITY.md](SECURITY.md).

## Product interface

The intended deployment has MCP on both sides of the application:

```text
Operator → Codex → Civitas MCP server → planning / Jury / execution services
                                      → procurement-provider MCP servers
```

Codex is responsible for conversation, intent capture, progress narration, and presenting approval requests. Civitas remains authoritative for typed inputs, evidence retrieval, optimization, Parliament, Jury and Dissent, replanning, freshness revalidation, approval binding, idempotency, and execution audit. A model or chat message cannot bypass those controls.

The primary interaction should be as small as:

```text
User: Protect seven days of demand across our warehouses while minimizing waste.
Codex: Civitas found false consensus on a stale lead-time source and replanned.
       The revised plan has Integrity 92/100 and all hard gates pass.
       Approve the exact plan for execution?
```

## Development

Prerequisites: Python 3.12, uv, Node.js 24, pnpm 10.31.0, and Docker Compose.

```bash
uv sync --frozen --all-groups
pnpm install --frozen-lockfile
docker compose up -d postgres
uv run alembic upgrade head
```

PostgreSQL listens on port `55432` by default to avoid colliding with a local installation. Set `CIVITAS_POSTGRES_PORT` to override it.

Run the required checks with:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest
pnpm format
pnpm lint
pnpm typecheck
pnpm build
```

Run the current demonstration API and optional audit viewer locally with:

```bash
uv run uvicorn civitas.api.app:create_app --factory --host 127.0.0.1 --port 8001
pnpm --filter @civitas/web dev
```

The Vite dev server proxies `/api` requests to `http://127.0.0.1:8001`.

`civitas.api.app:create_app` is the local, simulated demonstration API. It uses no live provider and performs no real procurement side effects. Keep it bound to loopback. The persistence-backed API factory in `civitas.api.guarded_api` requires a bearer token of at least 32 characters and an organization binding when it is composed by a deployment.

## Demo

The current end-to-end integration demo is a false-consensus case file. It starts with supplier A winning on shared stale evidence, routes through a clean-room Dissent check, reopens planning, approves supplier B after fresh public evidence, then performs freshness revalidation and duplicate-protected MCP execution.

Run the golden suite with:

```bash
uv run pytest tests/golden/test_scenarios.py -q
```

Run the integration-focused checks with:

```bash
uv run pytest tests/integration/test_demo_api.py tests/unit/execution/test_service.py tests/contract/test_mcp_integration.py -q
```

For the offline viewer demonstration, open `http://127.0.0.1:5173`, choose `False consensus with clean-room dissent`, and start the run. The SSE stream is produced while the scenario executes. The event docket should show this sequence:

```text
evidence retrieval
→ Parliament proposal / challenge / concession
→ Jury investigate
→ clean-room Dissent evidence
→ replanning
→ Jury approve
→ freshness revalidation
→ execution succeeded
→ duplicate execution downgraded to duplicate
```
