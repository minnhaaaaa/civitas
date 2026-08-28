# Agent 2 handoff — live/workflow-persistence

## Outcome

The branch provides the production PostgreSQL implementation of
`WorkflowCheckpointStore`, a durable `SKIP LOCKED` queue with expiring leases,
atomic checkpoint/event commits, abandoned-work recovery, and a worker CLI.

## Integration points

- Construct `PostgreSQLWorkflowCheckpointStore(database.sessions)` and inject it
  wherever the `WorkflowCheckpointStore` port is required.
- A worker composition factory must return (or asynchronously return) a fully
  configured `DurableWorkflowWorker`.
- Start a worker with:

  ```bash
  CIVITAS_WORKER_FACTORY=civitas.runtime:create_worker uv run civitas-worker
  ```

  `civitas.runtime:create_worker` is an example composition path; Agent 1 should
  supply the actual factory without changing the worker CLI contract.
- `scripts/run_worker.py` is retained as a compatibility wrapper around the same
  CLI. `--once` is available for jobs and operational probes.
- The existing synchronous `civitas.api.guarded_api.WorkflowStore` is not
  silently replaced. The runtime composition should route new durable planning
  runs through this branch's checkpoint store/worker so there is only one
  execution path.

## Persistence behavior

- `workflow_checkpoints` stores the full validated checkpoint JSON and indexed
  phase, cycle, event cursor, completion, availability, attempt, and lease data.
- Claims use `SELECT ... FOR UPDATE SKIP LOCKED`; different workers can process
  different runs, but only one current lease token can commit a given run.
- A transition checkpoint and its contiguous `workflow_events` are committed in
  one transaction. The planning-run status is updated in that transaction.
- Expired owners cannot commit. Recovery clears expired leases and makes the run
  available again; a process crash therefore loses at most the in-flight
  transition, not the last committed state.
- Resumable transitions remain side-effect free. Execution writes still belong
  behind Agent 4's idempotent execution ledger.

## Migration note

Migration `d3f1a6b8c902` currently follows `8e4c1d9a2b70`. If another workstream
adds a migration from that same revision before integration, rebase this
migration's `down_revision` or add an intentional Alembic merge revision so the
assembled repository retains a single head.

## Verification

Executed against the local PostgreSQL 17 Compose service:

```text
ruff format --check .       151 files formatted
ruff check .                passed
mypy                        79 source files passed
pytest -q                   120 passed
alembic upgrade head        d3f1a6b8c902 applied
```

The new integration cases cover concurrent claims, restart/resume persistence,
expired-lease recovery, stale-owner rejection, and monotonic event cursors.
