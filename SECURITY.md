# Security and integration status

This repository separates the public local demonstration from the guarded, persistence-backed execution boundary.

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

## Injection review

The audit found no raw SQL assembled from request, model, or MCP values. Persistence uses SQLAlchemy expressions and bound parameters. JSON fields are data, not executable SQL. Continue to prohibit `text()` or driver-level string interpolation with untrusted values.

Model output is treated as untrusted: responses are capped at 1 MiB, parsed as JSON, required to have an object root, and checked for required fields, additional properties, enums, constants, combinators, lengths, patterns, array bounds, uniqueness, and numeric bounds. Models never cross the execution authorization boundary directly. Prompt text is not a security control; tool permissions, typed contracts, deterministic verification, Jury gates, and idempotent execution remain authoritative.

## Known integration limitations

- The generic Parliament workflow currently uses deterministic role implementations. The Groq adapter is contract-tested but is not yet composed into those roles for explanatory challenge text.
- The generic investigation transition accepts a replanner callback; the demo performs real read-only MCP investigation around that transition. A production composition still needs a durable investigation worker.
- `compile_langgraph()` describes the approved topology, while durable resume is implemented by PostgreSQL workflow-event snapshots rather than a LangGraph checkpointer.
- Generic repositories are infrastructure primitives and are not tenant-safe API surfaces by themselves. Tenant ownership is enforced in guarded API and execution services; future repository APIs should require organization scope before broader reuse.
- The guarded API factory is tested but is not the same app as the unauthenticated offline demo factory. Deployment composition and operator identity remain environment-specific.

These limitations mean the offline demonstration is end-to-end, but the repository is not yet a production-ready autonomous procurement deployment.
