# Deployment

Civitas runs its product interface as an authenticated inbound MCP server with a
separate durable worker and an outbound procurement-MCP boundary. PostgreSQL is
the system of record. The audit viewer is optional and never authorizes or
executes procurement.

## Deterministic local stack

Copy the non-secret local template, choose a local-only password, then start the
migrated demo stack:

```bash
cp .env.example .env
docker compose -f deploy/compose.local.yaml --profile demo up --build
```

The `migrations` job must complete before `api` starts; the API health check is
therefore false until PostgreSQL is healthy and Alembic succeeds. Confirm it
with `curl -fsS http://127.0.0.1:8000/health`. The `demo` profile serves the
deterministic demonstration API and does not contact a real provider or create
real purchase orders.

Include the MCP server and durable worker with:

```bash
docker compose -f deploy/compose.local.yaml --profile mcp up --build
```

The MCP server and durable worker use the production composition and PostgreSQL
checkpoint queue. The simulated provider remains an explicit offline test
boundary and never authorizes real procurement. Provider reads and writes fail
closed unless `CIVITAS_PROVIDER_FACTORY` names an organization-owned
`module:callable` that returns `ProviderRuntimeDependencies`. Its planning
connection must contain the credential-isolated read-only Dissent client; its
execution connection is used only by guarded execution after approval,
freshness, locking, and idempotency checks. Set
`CIVITAS_LIVE_PROVIDER_REQUIRED=true` to make a missing factory a startup error.

An external simulator process is optional and intentionally separate. Start it
with `--profile provider-simulator` only after setting
`CIVITAS_SIMULATED_PROVIDER_COMMAND` to the simulator server executable. Enabling
that process does not connect it to Civitas; the configured provider factory
still owns capability discovery and credential-scoped client construction.

Worker leases renew while a transition is running. Configure
`CIVITAS_WORKER_LEASE_SECONDS` for the expected provider latency and
`CIVITAS_WORKER_MAX_ATTEMPTS` for the poison-work bound. Exhausting that bound
persists an `ESCALATE` checkpoint and monotonic failure event.

Stop services without deleting the audit database:

```bash
docker compose -f deploy/compose.local.yaml down
```

For a deterministic demo reset (this deletes local PostgreSQL data only):

```bash
docker compose -f deploy/compose.local.yaml down --volumes
```

## Production profile

Use the production overlay with a managed PostgreSQL service in a real
environment. Never publish the database port or place TLS keys in the Civitas
container.

```bash
docker compose \
  -f deploy/compose.local.yaml \
  -f deploy/compose.production.yaml \
  --profile mcp up -d
```

The production overlay deliberately requires `CIVITAS_PROVIDER_FACTORY` and
valid non-development secrets. Runtime validation also rejects stdio transport,
non-JSON logging, missing roles, shared bearer/approval secrets, and known
development database passwords in production.

## Kubernetes

The Kustomize base contains a one-shot migration Job, independently scalable
MCP and worker Deployments, restrictive pod security settings, health probes,
a default-deny NetworkPolicy, and a ClusterIP Service:

```bash
kubectl apply -k deploy/kubernetes/base
```

Before applying it, create a `civitas-runtime` Secret using an external secret
controller or your platform secret store. `deploy/kubernetes/secret.example.yaml`
documents the required keys but is intentionally excluded from Kustomize.
Replace the provider factory placeholder and database host, review the egress
allow-list for the selected provider, and apply the example TLS Ingress only
after configuring the cluster issuer and public hostname. Wait for the migration
Job to succeed before rolling out the worker and MCP Deployments. Their init
containers also fail closed until Alembic reports every configured head applied.

Terminate TLS at a reverse proxy or load balancer and forward only authenticated
HTTPS traffic to the Streamable HTTP MCP endpoint. The included high-entropy
bearer verifier supports expiry, revocation-ready credential records, roles,
correlation, and local throttling; configure an OAuth/JWT verifier and shared
rate limiter for remote multi-tenant service. STDI/O
is for local Codex use and must not be Internet-exposed. Authenticate the
inbound Codex connection separately from each outbound provider connection.

Inject `POSTGRES_PASSWORD`, `CIVITAS_BEARER_TOKEN`, provider credentials, and
OAuth configuration through the deployment platform's secret manager at runtime.
Do not place them in images, Compose files, command lines, source control, MCP
responses, or logs. Rotate one credential at a time: deploy the replacement,
verify authenticated health/readiness and one read-only provider call, revoke the
old secret, then retain only redacted audit references.

`CIVITAS_BEARER_TTL_SECONDS`, `CIVITAS_RATE_LIMIT_REQUESTS`, and
`CIVITAS_RATE_LIMIT_WINDOW_SECONDS` bound the included bearer deployment. The
defaults are 3600 seconds, 120 requests, and 60 seconds respectively.

## Health, readiness, and shutdown

- PostgreSQL readiness uses `pg_isready`.
- The migration job is the readiness gate for dependent services.
- The optional demo API health probe calls `/health`.
- `GET /health/live` reports process liveness without authentication.
- `GET /health/ready` verifies database connectivity, the exact Alembic head,
  and a recent durable worker heartbeat when worker readiness is required.
- `GET /metrics` exposes low-cardinality Prometheus metrics without tenant or
  operator labels. These operational endpoints do not expose MCP operations;
  `/mcp` remains authenticated and rate limited.
- Containers use an init process, finite grace periods, and a signal-forwarding
  entrypoint. Workers must checkpoint before exit; the execution ledger prevents
  a resumed worker from repeating an outbound write.

Readiness means that migrations and required dependencies are available; it
never means that a stale plan may execute. Execution still performs freshness
revalidation and approval/hash/idempotency checks.

## Logs and correlation

The runtime emits structured JSON logs in production and redacts bearer values
and URL passwords. Every HTTP request accepts a valid W3C `traceparent` or
creates one and returns it in the response. Every inbound MCP call also creates
or accepts a validated correlation ID and carries it through the planning run, approval
challenge/receipt, outbound MCP call, and immutable execution-audit record.
Log stable IDs and reason codes, never tokens, raw approval challenges,
authorization headers, database URLs, provider credentials, raw evidence
payloads, or SQL errors. Retain audit records according to organization policy;
application logs are an observability aid, not the audit source of truth.

## Backups and recovery

Follow the tested [backup and restore runbook](operations/BACKUP_RESTORE.md).
The provided scripts create a protected custom-format dump with a migration
revision sidecar and checksum, and refuse to restore into a non-empty schema.
Database restore does not recover external provider side effects, so reconcile
provider references from the execution audit before replaying any failed action.

## Optional audit viewer

The viewer can be deployed behind the same identity-aware reverse proxy, but it
is optional. It receives read-only, organization-scoped audit links; disabling
or losing the viewer cannot block MCP planning or guarded execution. There is no
viewer endpoint for approval or execution.
