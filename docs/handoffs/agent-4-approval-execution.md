# Agent 4 handoff — approval and guarded execution

Branch: `live/approval-execution`

## Delivered

- Product-facing persisted approval and execution adapters in
  `civitas.application.live_execution`.
- Agent 3 provider-boundary integration through a per-execution connection
  factory. The execution credential is connected only after the persisted
  receipt is loaded, and every write carries protected `_civitas_execution`
  metadata for the execution ID, receipt ID, and exact selected-plan hash.
- One canonical selected-plan hash and exact approval-total calculation shared
  by the facade and execution boundary.
- Approval-receipt consumption inside the guarded execution transaction,
  bound to organization, operator, planning run, selected-plan hash, approval
  policy, totals, and idempotency key.
- Concurrent execution freshness refresh followed by Jury gates, local
  warehouse/supplier row locks, and FEFO inventory reservations before writes.
- Durable provider-write ledger with per-supplier idempotency keys and explicit
  success or compensation-required state.
- Append-only execution transition events and organization-scoped paginated
  audit retrieval.
- Alembic revision `2c6d0a76f945` and PostgreSQL integration coverage.

## Composition notes

Construct one `ApprovalService`, wrap it with `PersistedApprovalAdapter`, and
pass the same service to `GuardedExecutionService(approvals=...)`. Wrap the
guarded service with `PersistedApprovedExecutionAdapter`; those two adapters
implement the facade's `ApprovalPort` and `ApprovedExecutionPort`.

Construct `OnboardedExecutionConnectionFactory` with Agent 3's
`ProviderOnboarder` and `ProviderRegistration`, then pass it as
`execution_connections` to `PersistedApprovedExecutionAdapter`. The adapter
derives `ExecutionProviderContext` from the persisted receipt; callers cannot
provide or override the provider binding metadata.

The provider adapter supplied to guarded execution must enforce write
idempotency remotely as well as locally. Provider writes remain reachable only
through this execution service.

## Merge notes

- Other live branches may add Alembic revisions from the same parent. Rebase
  this migration onto the integrated migration head or add a reviewed merge
  revision; retain a single Alembic head.
- Runtime composition should use `approval-v1` for approval receipts and keep
  Decision Integrity policy versioning separate.
- The legacy in-memory execution service remains for its existing unit contract;
  production composition should use `civitas.execution.guarded.GuardedExecutionService`.

## Verification

- `ruff check`: passed
- `mypy` on changed execution/approval modules: passed
- unit plus provider/security contract suite: 75 passed
- PostgreSQL guarded-execution integration suite: 10 passed
- Alembic: one head (`2c6d0a76f945`)
