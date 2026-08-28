# Agents 1–4 combined handoff

Branch: `live/agents-1-3-integration`

This branch now combines the production runtime, durable workflow, outbound
provider boundary, and approval/guarded-execution workstreams. It supersedes
the earlier Agents 1–3 integration handoff.

## Integrated execution path

`build_runtime` remains fail-closed unless it receives a complete
`ProviderExecutionRuntime`. With those dependencies it composes:

1. Agent 3's advertised read-only provider client for concurrent execution
   freshness checks;
2. the persisted `ApprovalService` shared by challenge issuance and guarded
   receipt consumption;
3. Agent 4's `GuardedExecutionService`; and
4. an execution-only provider connection created from the persisted receipt.

Every outbound write connection is bound to the generated execution ID,
persisted approval receipt ID, and canonical selected-plan hash. The provider
client adds that protected metadata to each write and rejects caller-supplied
binding metadata.

## Integration defects found and fixed

- Rebased the guarded-execution migration onto Agent 2's durable workflow
  migration, retaining one linear Alembic head.
- Replaced the runtime's provisional approval adapter with the persisted
  approval adapter and kept `approval-v1` separate from Decision Integrity
  policy versioning.
- Made Agent 3's evidence client implement the shared read-only MCP port used
  by the execution freshness gate; non-advertised and write calls fail closed.
- Added a transactional projection from durable workflow checkpoints into the
  normalized candidate-plan, plan-line, and Jury tables consumed by guarded
  execution. A Jury decision that does not match Parliament's deterministic
  selected solver plan is rejected.
- Aligned guarded execution with Agent 2's canonical
  `ready_for_approval` planning-run state.
- Preserved organization/operator receipt scope, exact plan hash and totals,
  approval consumption, local capacity locks and FEFO reservations, provider
  idempotency, immutable execution events, and compensation-required states.
- Hardened integration fixtures so projected execution records are removed in
  foreign-key-safe order and Jury fixtures approve the plan Parliament actually
  selected.

## Remaining production seams

- Production deployments must supply concrete `ProviderCredentialResolver`,
  `ProviderTransportFactory`, provider registration, and execution connection
  factory implementations. Registrations persist credential references, never
  raw credentials.
- Identity is still the controlled bearer implementation. Agent 5 must provide
  production JWT/OAuth verification, role resolution, tenant scoping, and rate
  limits.
- Agent 6 should persist the newly refreshed execution evidence and lineage in
  the canonical evidence graph and provide the production Jury/Dissent
  dependencies. Execution currently evaluates the fresh typed bundle and
  records the execution decision, but the refresh bundle itself is not yet
  projected into the evidence tables.
- Missing provider execution dependencies still select `DisabledExecutionPort`;
  no provider write is reachable through the fallback runtime.

## Verification

- Fresh PostgreSQL database migration: passed with one Alembic head,
  `2c6d0a76f945`.
- Ruff format and lint: passed.
- Strict mypy: passed for 90 source files.
- PostgreSQL integration suite: 30 passed.
- Complete repository suite: 147 passed.
- `git diff --check`: passed.

