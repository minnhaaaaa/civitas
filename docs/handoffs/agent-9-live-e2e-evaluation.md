# Agent 9 handoff — live E2E evaluation

Branch: `live-e2e-evaluation`

## Delivered

- A production-composed local stack with migrated PostgreSQL, durable worker,
  authenticated Streamable HTTP MCP, idempotent tenant/catalog provisioning,
  and a made-up operational provider.
- Provider-led planning from six typed reads: inventory, demand, supplier
  offers, lead times, warehouse capacity, and transport capacity.
- Initial and investigation evidence is persisted as canonical lineage before
  Jury evaluation; missing support fails closed and drives solver-owned replan.
- Replan-safe versioned solver plan IDs, phase-safe checkpoint projection, and
  accumulated evidence links across deduplicated provider observations.
- Persistence-stable selected-plan hashing and provider-independent duplicate
  execution lookup before reconnection.
- Local-only mock purchase-order writes with capability discovery, isolated
  planning/Dissent/execution credentials, and deterministic idempotency.
- `scripts/mcp_purchase_demo.py` exercises authenticated initialize, planning,
  Jury/replan, approval challenge, receipt, freshness recheck, write, and
  duplicate retry through the public MCP protocol.
- A Codex Streamable HTTP configuration template and real-provider factory
  onboarding guide. No bearer or provider credential is committed.
- Kubernetes MCP replicas default to one while Streamable HTTP sessions remain
  process-local.

## Acceptance evidence

- `ruff check src tests scripts/mcp_purchase_demo.py`: pass
- `mypy src`: pass (108 source files)
- unit + contract + end-to-end + non-PostgreSQL integration selection:
  134 passed
- migrated isolated PostgreSQL integration suite: 35 passed
- Docker Compose black-box result:
  - planning status `ready_for_approval`
  - Decision Integrity `85.0`, hard gates passed
  - execution state `succeeded`
  - external reference `simulated-po:...`
  - repeated request returned the same receipt with `duplicate=true`

## Deployment boundary

The included provider is deliberately rejected in production. A real deployment
must package and configure an organization-owned
`CIVITAS_PROVIDER_FACTORY=module:callable` as documented in
`docs/PROVIDER_ONBOARDING.md`. That factory is the only supported extension
point; it does not bypass approval, freshness, reservations, Jury, or the
execution ledger.
