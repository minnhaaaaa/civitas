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
