# Civitas Codex MCP demo

This local demo uses a made-up operational MCP provider. It exposes typed reads
for inventory, demand, supplier offers, lead times, warehouse capacity, and
transport capacity, plus one idempotent purchase-order write. The simulator is
refused when `CIVITAS_ENV=production`.

## Start the complete stack

```bash
cp .env.example .env
# Replace every change-me value in .env.
docker compose -f deploy/compose.local.yaml --profile mcp up -d --build
docker compose -f deploy/compose.local.yaml --profile mcp ps
```

Migrations run first, `civitas-demo-seed` idempotently provisions the made-up
organization/catalog, the durable worker starts, and then the authenticated
Streamable HTTP server becomes ready at `http://127.0.0.1:8001/mcp`.

Run the black-box acceptance client inside the image:

```bash
docker compose -f deploy/compose.local.yaml exec mcp-server \
  python scripts/mcp_purchase_demo.py --auto-approve
```

`--auto-approve` is simulator-only. Without it the client stops after creating
the immutable, short-lived approval challenge.

## Connect Codex

Export the same bearer token used by the server, then copy the table from
`deploy/codex.config.toml.example` into `~/.codex/config.toml` or the trusted
project's `.codex/config.toml`:

```bash
export CIVITAS_CODEX_BEARER_TOKEN='<the CIVITAS_BEARER_TOKEN value>'
```

Restart Codex and use `/mcp` to confirm `civitas` is connected. This follows the
[official Codex MCP configuration](https://developers.openai.com/codex/mcp/):
Streamable HTTP servers use `url`, and `bearer_token_env_var` supplies the
Authorization bearer without committing it.

Then type:

> Use Civitas to satisfy tomorrow's demand for `sku-local` at
> `warehouse-local`, minimizing cost and waste. Investigate and replan as
> needed. Show me the selected plan and approval challenge before purchasing.

Codex should call `plan_procurement_goal`, poll `get_planning_run`, and read
`get_decision_summary`. It may autonomously investigate and replan. It must show
the exact challenge and receive operator approval before calling
`approve_execution` and `execute_approved_plan`. Reusing the same idempotency key
returns the original receipt and never creates a second order.

The required evaluation also covers a false-consensus case: clean-room Dissent
must discover correlated or stale evidence and force a solver-owned replan
before the approval gate. The duplicate retry remains bound to the same
approval receipt and idempotency key.

## Connect a user's provider

Set `CIVITAS_PROVIDER_FACTORY=your_package.bootstrap:create_dependencies` and
follow [provider onboarding](PROVIDER_ONBOARDING.md). This replaces the made-up
provider while retaining the same evidence, Jury, approval, freshness, and
execution controls.
