# Security and integration status

This repository separates the public local demonstration from the guarded, persistence-backed execution boundary. The approved product direction adds an inbound Civitas MCP server for Codex while retaining outbound MCP clients for procurement providers. These are separate trust boundaries.

## Inbound agent boundary

Codex is a presentation and intent-capture layer, not an execution authority. The inbound MCP server may expose only bounded application workflows and read-only status/audit retrieval plus the guarded approval sequence. It must not expose generic repositories, arbitrary query execution, provider credentials, unrestricted operational tools, or direct inventory and order mutations.

Every call must derive organization and operator identity from authenticated deployment context rather than model-supplied identifiers. Structured arguments remain untrusted input and receive the same strict validation, ownership checks, limits, and audit treatment as guarded HTTP requests. Remote multi-tenant MCP deployments require OAuth; controlled single-tenant deployments may use rotated bearer credentials over TLS.

The production bearer adapter stores only SHA-256 credential digests, compares
them in constant time, and enforces activation, expiry, and revocation. Each
credential resolves immutable organization, operator, subject, and role claims.
Streamable HTTP validates or creates a correlation ID, rate-limits by resolved
tenant/operator, and emits authentication audit events without credential values.
Intent-tool authorization is deterministic; an execution-capable role still
cannot bypass the immutable approval receipt and guarded execution checks.

The Codex-facing server and procurement-provider clients must not share credentials. Dissent keeps read-only outbound credentials and isolated cache namespaces. Execution obtains only the minimum outbound write capability required for the approved action.

## Local demonstration

`civitas.api.app:create_app` is an offline simulation. It has no authentication because it cannot reach a real provider and its MCP writes target only an in-process mock server. Bind it to `127.0.0.1`, as shown in the README. Run identifiers are non-guessable UUIDs, runs are retained in a bounded store, and request bodies use strict Pydantic contracts.

## Guarded API

`civitas.api.guarded_api:create_guarded_app` is the deployment-facing factory. Composition requires:

- a bearer token containing at least 32 characters;
- one configured organization identity;
- a migrated PostgreSQL-backed workflow store; and
- the persistence-backed guarded execution service.

Every guarded route authenticates before lookup and verifies that the planning run belongs to the configured organization. A deployment should terminate TLS in front of this app, source tokens from a secret manager, rotate them, and add operator-level authorization if multiple users share one organization.

## Execution invariants

Before an MCP write, execution verifies that the run is approved, the selected solver plan belongs to that run, the Jury decision belongs to that run and plan, the policy version and approval threshold match, every required hard gate passed, and Dissent robustness was recorded. Supplier, SKU, warehouse, and lot references are checked against the run organization.

Idempotency is scoped by organization and serialized with a PostgreSQL transaction advisory lock. Every MCP write also requires a non-empty idempotency key. Mutable facts are refreshed, freshness TTLs are enforced, feasibility-sensitive capacities and prices are compared, inventory reservations use transactional FEFO allocation, and partial provider failures produce a compensation-required audit state.

Conversational confirmation does not weaken these invariants. The inbound MCP flow must first issue a short-lived challenge bound to the immutable plan hash, organization, operator, approved totals, and policy version. A material refresh change invalidates the approval and returns the workflow to investigation or escalation.

## Injection review

The audit found no raw SQL assembled from request, model, or MCP values. Persistence uses SQLAlchemy expressions and bound parameters. JSON fields are data, not executable SQL. Continue to prohibit `text()` or driver-level string interpolation with untrusted values.

Model output is treated as untrusted: responses are capped at 1 MiB, parsed as JSON, required to have an object root, and checked for required fields, additional properties, enums, constants, combinators, lengths, patterns, array bounds, uniqueness, and numeric bounds. Models never cross the execution authorization boundary directly. Prompt text is not a security control; tool permissions, typed contracts, deterministic verification, Jury gates, and idempotent execution remain authoritative.

## Known integration limitations

- The Codex-facing inbound MCP adapter and transport-neutral product facade are implemented and covered by deterministic end-to-end contract tests. A deployable composition root that wires them to PostgreSQL workflow persistence, the durable worker, production identity resolution, and a real provider is not yet supplied. The current runnable product surface remains the offline demonstration API and optional React viewer.

- The generic Parliament workflow currently uses deterministic role implementations. The Groq adapter is contract-tested but is not yet composed into those roles for explanatory challenge text.
- The generic investigation transition accepts a replanner callback; the demo performs real read-only MCP investigation around that transition. A production composition still needs a durable investigation worker.
- `compile_langgraph()` describes the approved topology, while durable resume is implemented by PostgreSQL workflow-event snapshots rather than a LangGraph checkpointer.
- Generic repositories are trusted migration/import primitives. Authenticated
  paths use the tenant-bound repository factory, which scopes direct rows by
  organization and scopes child inputs through their owning planning run.
- The guarded API factory is tested but is not the same app as the unauthenticated offline demo factory. Deployment composition and operator identity remain environment-specific.

These limitations mean the offline demonstration is end-to-end, but the repository is not yet a production-ready autonomous procurement deployment.
