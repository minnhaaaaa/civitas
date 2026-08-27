# Codex/MCP implementation work plan

## Objective

Deliver Civitas as a deployable Codex-compatible MCP procurement agent while preserving the existing React application as an optional read-only audit viewer.

The integrated acceptance path is:

```text
operator goal in Codex
→ inbound Civitas MCP call
→ durable evidence retrieval
→ solver alternatives
→ Parliament
→ Jury investigation
→ clean-room Dissent
→ replanning
→ decision summary
→ immutable approval challenge
→ explicit operator approval
→ freshness revalidation
→ idempotent outbound MCP execution
→ execution receipt and optional audit link
```

## Coordination rules

- Agent 0 owns all new shared contracts and ports. Merge it before other MCP branches are integrated.
- Agent 3 is the only agent allowed to create an Alembic migration in this workstream.
- Agents 1, 2, 4, and 5 develop against Agent 0 contracts with deterministic fakes and may work concurrently after Agent 0.
- No agent may create a second execution implementation. HTTP, MCP, and the optional viewer must delegate to the same guarded application services.
- Inbound Codex tools must never expose generic repositories, arbitrary SQL, direct provider credentials, or unrestricted outbound MCP tools.
- Codex conversation is not authorization. Only the persisted, short-lived approval challenge authorizes the exact immutable plan hash.
- Dissent remains read-only and isolated from Parliament memory, credentials, and tool-result caches.
- Required tests use no live model, network service, or procurement provider.
- Each branch must include tests and a handoff listing assumptions, contract versions, migrations, and unresolved integration points.
- Do not redesign existing optimization, Jury, evidence, or execution contracts unless Agent 0 coordinates and documents the amendment.

## Agent 0 — MCP contracts and application ports

Branch: `mcp/foundation-contracts`

This branch is the merge gate for the new work.

Owned paths:

```text
src/civitas/contracts/mcp_product.py
src/civitas/ports/product_service.py
src/civitas/ports/identity.py
tests/unit/contracts/test_mcp_product.py
MCP_INTERFACE.md
```

Responsibilities:

- Define strict request and response contracts for all inbound tools.
- Define run status, decision summary, business impact, approval challenge, approval receipt, execution receipt, and audit-link contracts.
- Define the application-service protocol consumed by MCP and HTTP adapters.
- Define authenticated organization/operator context without importing MCP, FastAPI, ORM, or provider types.
- Version tool and approval contracts.
- Define error codes for invalid input, not found, conflict, expired approval, stale data, investigation required, escalation, rejected execution, and duplicate execution.
- Specify payload-size, pagination, cursor, and timestamp requirements.

Acceptance criteria:

- Contracts reject unknown fields, invalid identifiers, non-finite numbers, unbounded goals, and malformed cursors.
- Approval contracts bind organization, operator, run, plan hash, policy version, totals, issued time, and expiry.
- Domain contracts contain no MCP SDK, FastAPI, SQLAlchemy, Groq, or Codex imports.
- JSON-schema snapshots are deterministic.
- Every downstream agent can work with an in-memory fake implementing the service protocol.

## Agent 1 — Inbound MCP server and transports

Branch: `mcp/inbound-server`

Owned paths:

```text
src/civitas/mcp_server/
tests/contract/mcp_server/
tests/integration/mcp_server/
```

Responsibilities:

- Implement the intent-level MCP tool server.
- Expose `plan_procurement_goal`, `get_planning_run`, `get_decision_summary`, `prepare_execution`, `approve_execution`, `execute_approved_plan`, and `get_execution_audit`.
- Support STDIO for local Codex demos and Streamable HTTP for deployment.
- Map application errors to stable, non-sensitive MCP responses.
- Add concise server instructions describing the safe tool sequence and approval rule.
- Add bounded read-only resources for larger evidence summaries if required.
- Keep transport and MCP SDK types outside domain and application contracts.

Acceptance criteria:

- Tool schemas exactly match Agent 0 contracts.
- Every tool delegates to the application-service port; no business logic is duplicated in handlers.
- STDIO startup and shutdown pass contract tests.
- Streamable HTTP rejects unauthenticated requests.
- Responses are bounded and do not leak credentials, SQL errors, stack traces, or cross-organization identifiers.
- The write tool cannot be called successfully without a valid approval receipt and idempotency key.

## Agent 2 — Procurement application facade

Branch: `mcp/application-facade`

Owned paths:

```text
src/civitas/application/
tests/unit/application/
tests/integration/application/
```

Responsibilities:

- Implement the single application-service facade used by MCP and guarded HTTP adapters.
- Translate a typed procurement goal into the existing workflow input.
- Compose evidence retrieval, optimizer alternatives, Parliament, Jury/Dissent, replanning, and execution services without duplicating their policies.
- Produce compact decision summaries and deterministic business-impact fields.
- Implement status polling and cursor-based progress retrieval.
- Return stable audit-view links as optional metadata, never as authoritative state.
- Preserve resumability and bounded-autonomy outcomes.

Acceptance criteria:

- One facade drives both the false-consensus golden scenario and independent-consensus scenario.
- Solver and Jury outputs—not conversational text—determine plan and decision state.
- Investigation reopens planning and produces a new selected-plan hash.
- Polling is repeatable and does not start duplicate runs.
- The facade can be tested entirely with fake repositories, clocks, IDs, models, and MCP clients.

## Agent 3 — Identity, approval challenge, and persistence

Branch: `mcp/approval-identity`

Owned paths:

```text
src/civitas/identity/
src/civitas/approval/
src/civitas/persistence/models.py
src/civitas/persistence/repositories.py
alembic/versions/<single_new_revision>.py
tests/unit/approval/
tests/integration/approval/
tests/integration/persistence/
```

Responsibilities:

- Derive organization and operator identity from authenticated transport context.
- Persist hashed, short-lived, single-plan approval challenges and approval receipts.
- Bind approval to organization, operator, run, selected plan hash, policy version, approved totals, and expiry.
- Atomically consume or validate an approval at execution while permitting idempotent retries of the same action.
- Reject cross-organization, changed-plan, expired, replayed-for-another-action, and over-limit approvals.
- Add the only Alembic migration permitted in this workstream.

Acceptance criteria:

- Raw approval secrets are never stored.
- Concurrent approval or execution attempts cannot exceed the approved action.
- A material plan refresh invalidates the old challenge.
- Organization and operator boundaries are enforced in repository queries, not only at the transport layer.
- Migrations build from an empty PostgreSQL database and maintain one Alembic head.

## Agent 4 — Durable workflow worker and progress delivery

Branch: `mcp/durable-worker`

Owned paths:

```text
src/civitas/worker/
src/civitas/workflow/checkpointing.py
src/civitas/workflow/progress.py
tests/unit/worker/
tests/integration/worker/
```

Responsibilities:

- Run planning and investigation outside the MCP request lifecycle.
- Claim work safely, recover abandoned work, and resume from durable workflow state.
- Implement the generic read-only outbound MCP investigation worker.
- Preserve bounded cycles, deadlines, repeated-evidence detection, and escalation.
- Produce compact material progress events suitable for Codex polling.
- Ensure retries cannot repeat an outbound write or create a second planning run.

Acceptance criteria:

- A process interruption can resume the run without losing lineage or repeating completed stages.
- Two workers cannot process the same transition concurrently.
- Investigation uses the Dissent read-only namespace and credentials.
- Polling observes monotonic event cursors.
- Exhausted bounds produce `ESCALATE`, never silent approval or an infinite loop.

## Agent 5 — Codex plugin, skill, and judge experience

Branch: `mcp/codex-plugin`

Owned paths:

```text
plugins/civitas/
docs/CODEX_DEMO.md
tests/contract/codex_plugin/
```

Responsibilities:

- Package the Civitas MCP server and a focused Codex skill/plugin manifest.
- Teach Codex when to start a run, when to poll, how to summarize evidence, and when to request approval.
- Keep instructions concise and place the safety-critical tool sequence first.
- Add local project configuration examples for STDIO without committing user-specific paths or secrets.
- Write a deterministic two-to-three-minute judge script and reset procedure.
- Ensure Codex offers the optional audit link only when deeper evidence inspection is useful.

Acceptance criteria:

- A fresh local checkout can connect Codex to the STDIO server using documented steps.
- The skill never tells Codex to fabricate quantities, scores, approvals, or execution success.
- The false-consensus demo can be driven entirely from a conversational goal.
- The judge script demonstrates a duplicate execution retry returning the original receipt.
- No credentials, absolute developer paths, or machine-specific configuration are committed.

## Agent 6 — Optional audit-view integration

Branch: `mcp/audit-viewer`

Owned paths:

```text
apps/web/
```

Responsibilities:

- Reframe the existing frontend as an optional audit viewer rather than the primary workflow.
- Support stable deep links from MCP decision and execution responses.
- Make run, plan, organization-safe public reference, and event cursor routable.
- Keep evidence graph, Integrity components, hard gates, replanning timeline, and execution receipt read-only.
- Remove or disable any UI path that could bypass the common guarded approval service.
- Preserve mock playback for offline demonstrations.

Acceptance criteria:

- Opening a valid audit link reconstructs the selected run and current event cursor.
- Invalid or unauthorized links reveal no run existence or organization data.
- The viewer remains optional; MCP flows pass when it is unavailable.
- Production build, type checking, accessibility checks, and reduced-motion behavior pass.

## Agent 7 — MCP security and evaluation suite

Branch: `mcp/security-evaluation`

Owned paths:

```text
evaluation/mcp/
tests/security/
tests/property/mcp/
tests/golden/mcp/
```

Responsibilities:

- Build adversarial tests for prompt injection, tool-result injection, schema abuse, oversized payloads, and malformed cursors.
- Test cross-organization access, confused-deputy attempts, approval replay, plan substitution, expiry, and duplicate execution.
- Add property tests for idempotency, monotonic cursors, approval invariants, and bounded payloads.
- Score conversational presentation separately from deterministic decision correctness.
- Add MCP-driven versions of the false-consensus and duplicate-execution golden scenarios.

Acceptance criteria:

- Untrusted goal, model, and provider text cannot select tools, alter policy, grant approval, or inject SQL.
- Cross-organization identifiers fail closed without existence disclosure.
- Changed plans and stale challenges cannot execute.
- Required tests make no network calls and are deterministic.
- MCP and guarded HTTP paths produce equivalent decision and execution outcomes.

## Agent 8 — Deployment and observability

Branch: `mcp/deployment`

Owned paths:

```text
Dockerfile*
docker-compose*.yml
deploy/
scripts/
docs/DEPLOYMENT.md
```

Responsibilities:

- Package the MCP HTTP server, worker, PostgreSQL, simulated procurement MCP provider, and optional audit viewer.
- Add health, readiness, migration, and graceful-shutdown behavior.
- Document TLS termination, OAuth/bearer configuration, secret rotation, backups, and demo reset.
- Add structured logs and correlation IDs across MCP call, planning run, approval, outbound tool call, and execution audit.
- Provide a deterministic local deployment profile and a production-oriented remote profile.

Acceptance criteria:

- One documented command starts a migrated local stack from a clean checkout.
- Readiness remains false until migrations and required dependencies are available.
- Secrets are injected at runtime and never baked into images or logs.
- Worker and server restarts preserve runs and approvals.
- The optional viewer can be omitted without breaking MCP operation.

## Agent 9 — End-to-end integration

Branch: `mcp/end-to-end`

Start after Agents 0–8 have merged in the prescribed order.

Owned paths:

```text
cross-cutting integration fixes
tests/end_to_end/mcp/
README.md
INTEGRATION_AUDIT.md
SECURITY.md
```

Responsibilities:

- Rebase and merge all MCP feature branches.
- Resolve adapter mismatches without redesigning approved contracts.
- Compose the production application facade, identity, persistence, worker, inbound server, and outbound simulated provider.
- Connect optional audit links to the deployed viewer.
- Run migrations, containers, all existing tests, MCP golden scenarios, security tests, and dependency audits.
- Verify the complete false-consensus demonstration through Codex/MCP.
- Update setup, demo, deployment, security, and audit documentation with measured results.

Acceptance criteria:

```text
Codex goal
→ authenticated inbound MCP
→ durable run
→ evidence retrieval
→ solver alternatives
→ Parliament
→ Jury investigation
→ clean-room Dissent
→ replanning
→ immutable approval challenge
→ explicit operator approval
→ freshness revalidation
→ idempotent outbound MCP execution
→ duplicate retry returns original receipt
→ optional evidence-audit deep link
```

The final handoff must distinguish deterministic demo readiness from production readiness and list every remaining external-provider or infrastructure dependency.

## Recommended merge order

1. `mcp/foundation-contracts`
2. `mcp/application-facade`
3. `mcp/approval-identity`
4. `mcp/durable-worker`
5. `mcp/inbound-server`
6. `mcp/codex-plugin`
7. `mcp/audit-viewer`
8. `mcp/security-evaluation`
9. `mcp/deployment`
10. `mcp/end-to-end`

Agents 1–8 may develop concurrently after Agent 0 using fakes. Before merge, each branch rebases onto all earlier merged dependencies in this list and reruns its owned tests.

## Required handoff template

Each agent reports:

```text
Branch and final commit:
Owned paths changed:
Contracts consumed and version:
Migrations added:
Tests run and results:
Security assumptions:
Integration assumptions:
Known limitations:
Required follow-up:
```
