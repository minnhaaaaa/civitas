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
docker compose -f deploy/compose.local.yaml up --build
```

The `migrations` job must complete before `api` starts; the API health check is
therefore false until PostgreSQL is healthy and Alembic succeeds. Confirm it
with `curl -fsS http://127.0.0.1:8000/health`. The local profile serves the
deterministic demonstration API and does not contact a real provider or create
real purchase orders.

After the inbound-server and durable-worker branches are assembled, include the
MCP workers and simulated provider:

```bash
docker compose -f deploy/compose.local.yaml --profile mcp up --build
```

Those services deliberately fail closed on this base branch rather than claim
they are ready. Their entrypoints are provided by the Agent 1 and Agent 4
deliverables. Configure their exact assembled commands at runtime via
`CIVITAS_MCP_SERVER_COMMAND`, `CIVITAS_WORKER_COMMAND`, and
`CIVITAS_SIMULATED_PROVIDER_COMMAND`; the wrappers `exec` those commands rather
than providing a second execution path. This avoids a fake worker that could
silently drop planning work.

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

Terminate TLS at a reverse proxy or load balancer, forward only authenticated
HTTPS traffic to the Streamable HTTP MCP endpoint, and configure an OAuth issuer
or a high-entropy bearer-token verifier there and in the inbound server. STDI/O
is for local Codex use and must not be Internet-exposed. Authenticate the
inbound Codex connection separately from each outbound provider connection.

Inject `POSTGRES_PASSWORD`, `CIVITAS_BEARER_TOKEN`, provider credentials, and
OAuth configuration through the deployment platform's secret manager at runtime.
Do not place them in images, Compose files, command lines, source control, MCP
responses, or logs. Rotate one credential at a time: deploy the replacement,
verify authenticated health/readiness and one read-only provider call, revoke the
old secret, then retain only redacted audit references.

## Health, readiness, and shutdown

- PostgreSQL readiness uses `pg_isready`.
- The migration job is the readiness gate for dependent services.
- The API container health probe calls `/health`; the product MCP server must
  expose a readiness endpoint that additionally checks its database, migration
  revision, authenticated identity configuration, and worker dependency.
- Containers use an init process, finite grace periods, and a signal-forwarding
  entrypoint. Workers must checkpoint before exit; the execution ledger prevents
  a resumed worker from repeating an outbound write.

Readiness means that migrations and required dependencies are available; it
never means that a stale plan may execute. Execution still performs freshness
revalidation and approval/hash/idempotency checks.

## Logs and correlation

Emit JSON logs in production. Every inbound MCP call creates or accepts a
validated correlation ID and carries it through the planning run, approval
challenge/receipt, outbound MCP call, and immutable execution-audit record.
Log stable IDs and reason codes, never tokens, raw approval challenges,
authorization headers, database URLs, provider credentials, raw evidence
payloads, or SQL errors. Retain audit records according to organization policy;
application logs are an observability aid, not the audit source of truth.

## Backups and recovery

Use encrypted, point-in-time PostgreSQL backups and routinely restore them into
an isolated environment. Back up the database and its migration revision
together. Test that a restored worker resumes a planning run without creating a
duplicate execution: execution retries must return the original immutable
receipt. Database restore does not recover external provider side effects, so
reconcile provider references from the execution audit before replaying any
failed action.

## Optional audit viewer

The viewer can be deployed behind the same identity-aware reverse proxy, but it
is optional. It receives read-only, organization-scoped audit links; disabling
or losing the viewer cannot block MCP planning or guarded execution. There is no
viewer endpoint for approval or execution.
