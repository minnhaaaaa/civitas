# Civitas

Civitas is an autonomous multi-agent food-procurement system that combines negotiation, evidence-lineage analysis, adaptive replanning, deterministic optimization, and guarded MCP execution.

Specialized Parliament agents investigate competing objectives and compare solver-generated procurement alternatives. An evidence-aware Jury then evaluates provenance, genuine source independence, contradictions, and adversarial dissent before an action can pass the execution safety boundary.

The project is currently in the foundation stage. See [PLAN.md](PLAN.md) for the delivery plan, [AGENTS.md](AGENTS.md) for behavioral and engineering requirements, and [TECH_STACK.md](TECH_STACK.md) for approved technologies.

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

Run the backend API and the viewer locally with:

```bash
uv run uvicorn civitas.api.app:create_app --factory --host 127.0.0.1 --port 8000
pnpm --filter @civitas/web dev
```

The Vite dev server proxies `/api` requests to `http://127.0.0.1:8000`.

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

Open the viewer at `http://127.0.0.1:5173`, choose `False consensus with clean-room dissent`, and start the run. The event docket should show this sequence:

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
