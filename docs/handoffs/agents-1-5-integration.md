# Agents 1–5 combined handoff

Integration target: `main`

## Integrated runtime

The production composition now joins:

1. the inbound intent-level MCP server and application facade;
2. PostgreSQL workflow checkpoints, leases, recovery, worker, and progress events;
3. provider capability onboarding with isolated planning, Dissent, and execution
   credentials;
4. persisted approval challenges/receipts and guarded provider writes; and
5. hashed bearer verification, tenant/operator/role identity, authorization,
   correlation, authentication audit propagation, and rate limiting.

The runtime is fail closed when provider execution dependencies are absent. When
they are present, the exact persisted receipt and selected-plan hash create the
execution-only provider connection. No direct provider write path was added.

## Integration corrections

- Linearized Alembic migrations through the durable workflow metadata and guarded
  execution ledger; one head remains.
- Activated Agent 5's verifier and role authorizer in the actual Streamable HTTP
  runtime instead of retaining the provisional plaintext token adapter.
- Added bounded bearer lifetime and configurable per-principal throttling.
- Preserved a static verified operator context only for local STDIO composition.
- Reject unsuccessful MCP write results before success can enter the provider
  ledger; they now produce compensation-required state.
- Made FEFO reservation idempotency unique and stable per distribution line.
- Enforced refreshed supplier lead time against the approved arrival bucket.
- Kept approval policy `approval-v1` separate from Decision Integrity policy.

## Remaining boundaries

- Production provider credential resolvers, transports, and registrations are
  deployment dependencies. The environment CLI does not invent them.
- Agent 6 must persist execution-refresh evidence into canonical lineage and
  compose durable clean-room Dissent/investigation with the production Jury.
- Remote multi-tenant deployment should replace the included opaque bearer with
  OAuth/JWT and replace the in-process limiter with a shared atomic backend.
- Offer expiry/quote tokens and provider-specific compensation operations must be
  enforced by each onboarded provider capability implementation.

## Verification

```text
ruff check src tests                              passed
mypy src/civitas                                 passed (95 source files)
pytest tests/unit tests/security tests/contract  112 passed
pytest tests/integration                         31 passed
pytest                                           155 passed
alembic heads                                    2c6d0a76f945 (single head)
```
