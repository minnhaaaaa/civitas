# Agent 1 handoff — runtime composition

Branch: `live/runtime-composition`

## Delivered

- `civitas.runtime` validates production configuration and assembles the database,
  OR-Tools optimizer, Parliament workflow, deterministic Jury, persisted approval
  service, product facade, controlled bearer identity, and inbound MCP server.
- `civitas-mcp` and `python -m civitas.mcp_server` replace the old unconditional
  failure entry point and support STDIO or authenticated Streamable HTTP.
- The composition accepts `WorkflowRunStore` and `ApprovedExecutionPort` overrides
  so the persistence and guarded-execution branches can integrate without changing
  public contracts.
- Default outbound execution is explicitly rejected; it cannot bypass approval or
  provider-write controls.

## Integration seams

1. Agent 2 should pass its PostgreSQL-backed `WorkflowRunStore` to `build_runtime`.
2. Agent 4 should pass its persisted guarded `ApprovedExecutionPort` to
   `build_runtime`; remove the default disabled port only after that adapter exists.
3. Agent 5 can replace `ControlledBearerIdentity` with its verifier while preserving
   the `resolve(token)` and current-operator boundary.
4. Agent 6 should replace `FailClosedJuryPort` with the persisted evidence and
   clean-room Dissent Jury adapter.

## Verification

Run:

```bash
uv run pytest tests/unit/runtime tests/contract/mcp_server -q
uv run ruff check src/civitas/runtime tests/unit/runtime
uv run mypy
```
