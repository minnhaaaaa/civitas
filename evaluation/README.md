# Evaluation

The evaluation package contains deterministic, versioned scenario bundles with separate hidden truth and system-visible observations. The default suite never calls a live model or MCP provider.

The ten required scenarios cover independent consensus, shared-source false consensus, an agent echo, stale lead-time contradiction, clean MCP evidence, genuine objective conflict, partial fulfillment, FEFO failure, warehouse capacity conflict, and duplicate execution retry.

Run the golden and generated-invariant layers independently:

```bash
uv run pytest tests/golden -q
uv run pytest tests/property -q
```

`evaluation.runner` executes the deterministic optimizer, independent verifier, lineage analysis, Jury policy, regret comparison, and execution-ledger simulation. `evaluation.metrics` reports solver, Jury, reason-code, lineage, regret, and retry results separately. Hidden claims and evidence are asserted to be disjoint from all visible fixtures.
