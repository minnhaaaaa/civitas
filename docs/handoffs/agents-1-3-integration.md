# Agents 1–3 combined handoff

Branch: `live/agents-1-3-integration`

This branch combines:

- Agent 1 production runtime composition and deployable inbound MCP entrypoint;
- Agent 2 PostgreSQL workflow runs, checkpoints, queue leases, recovery, and
  worker CLI; and
- Agent 3 outbound provider capability, typed evidence, credential-isolation,
  retry/timeout, Dissent, simulator, and onboarding boundaries.

## Review fixes included

- `planning_runs.status` now always uses canonical product states rather than
  internal Parliament phase names.
- Active workers renew their opaque PostgreSQL lease while a transition runs.
  A renewal failure cancels the transition and prevents a stale commit.
- Lease attempts are durable. `CIVITAS_WORKER_MAX_ATTEMPTS` bounds repeated
  infrastructure failures; exhaustion atomically records a terminal
  `ESCALATE` checkpoint and contiguous `run.failed` progress event.
- Worker cancellation releases its lease and cancels both transition and
  heartbeat tasks.

## Remaining integration seams

- Agent 4 must replace the disabled execution port with persisted approval
  receipts, guarded freshness/reservation transactions, and the execution
  ledger. Only that service should receive Agent 3's write-capable provider
  client.
- Production provider deployments must implement `ProviderCredentialResolver`
  and `ProviderTransportFactory`; registrations persist references, never raw
  secrets.
- Production multi-tenant deployments still require the identity workstream's
  OAuth/JWT verifier in place of controlled bearer identity.

Normal planning remains fail-closed: missing Jury/Dissent/provider execution
integration cannot become approval or a provider write.

## Verification

- Ruff lint and format checks pass.
- Strict mypy passes for 88 source files.
- All 141 tests pass against a freshly migrated PostgreSQL database.
- Alembic reports one applied head: `f7b2c4d6e801`.
