# Agent 7 handoff — live deployment and observability

Branch: `live/deployment-observability`

Base: local `main` at `173d4c5`, which integrates Agents 1–6.

## Delivered

- Added validated production configuration, secret-file-capable runtime settings, Kubernetes
  Secret references, and an explicit `module:callable` provider bootstrap. Live-provider mode fails startup unless the
  factory supplies both credential-isolated planning/Dissent reads and approval-guarded execution
  dependencies; provider writes still have no alternate path.
- Added an Alembic-backed service-heartbeat table, worker presence reporting, fast container
  probes, abandoned-worker readiness detection, and MCP liveness/readiness endpoints. Readiness
  checks PostgreSQL, the exact migration head, and a recent worker heartbeat when configured.
- Added secret-safe JSON request/authentication logs, validated W3C trace propagation, correlation
  fields, and low-cardinality Prometheus metrics without organization/operator labels.
- Completed the non-root, signal-forwarding Docker image and local/production Compose lifecycle.
  The MCP profile now runs migrations, waits for a healthy durable worker, and then exposes the
  authenticated server. An external simulator is a separate opt-in profile.
- Added a Kubernetes Kustomize base with a migration Job, migration-gated worker/server pods,
  readiness/liveness probes, security contexts, resource bounds, default-deny networking,
  ClusterIP service, and secret/TLS examples that are intentionally not deployed by default.
- Added guarded PostgreSQL backup/restore scripts and a recovery runbook covering checksums,
  migration revision, empty-target restore, execution-ledger reconciliation, and duplicate-write
  verification.
- Corrected two integration defects discovered by the assembled-stack smoke test: Starlette 1.6
  lifecycle composition now preserves FastMCP's lifespan, and the worker health probe avoids
  importing the full optimizer/runtime under a five-second probe deadline.

## Migration

New Alembic head: `c72e4a8b901d`.

It adds `service_heartbeats`; the table is operational presence only and never grants approval or
execution authority.

```bash
uv run alembic upgrade head
```

## Provider bootstrap contract

Set `CIVITAS_PROVIDER_FACTORY=package.module:create_dependencies`. The callable receives
`RuntimeSettings` and returns (or asynchronously resolves to) `ProviderRuntimeDependencies`.
Production deployment sets `CIVITAS_LIVE_PROVIDER_REQUIRED=true`, so a missing, invalid, or
incomplete factory exits with configuration status 78. Agent 3's organization-owned onboarding
implementation must supply the concrete transport and secret-manager integration; this branch does
not fabricate credentials or add direct provider writes.

## Operations

- MCP liveness: `GET /health/live`
- MCP readiness: `GET /health/ready`
- Prometheus scrape: `GET /metrics`
- MCP product endpoint: `/mcp` (still bearer-authenticated and rate-limited)
- Backup/restore: `docs/operations/BACKUP_RESTORE.md`
- Kubernetes base: `deploy/kubernetes/base`

## Validation

- complete suite against empty migrated PostgreSQL 17: `178 passed`
- post-smoke focused runtime/worker/deployment tests: `34 passed`
- Ruff check/format and strict mypy: clean
- local and production Compose configuration: valid
- Docker image build: successful; runtime user `civitas`, ports 8000/8001
- assembled local stack: migrations completed, worker healthy, `/health/live` 200,
  `/health/ready` 200, `/metrics` 200, unauthenticated `/mcp` 401

The two disposable PostgreSQL volumes and all smoke-test containers were removed after validation.

## Follow-up boundaries

- Agent 8 may consume read-only audit links only; it must not reuse the unauthenticated operational
  endpoints as an audit-data path.
- Agent 9 should supply the real test provider factory, run production validation with worker
  readiness enabled, assert trace/correlation propagation, and exercise backup-restore duplicate
  execution reconciliation.
- Any later workstream that adds an Alembic head must update `EXPECTED_DATABASE_REVISION` and the
  deployment migration-gate expectations in the same change; readiness intentionally requires the
  exact deployed schema revision.
- A horizontally scaled production deployment should replace the included process-local rate
  limiter and metrics accumulator with shared/platform implementations; neither affects approval
  or execution correctness.
