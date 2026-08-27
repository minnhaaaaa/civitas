# Evaluation

The evaluation package contains deterministic, versioned scenario bundles with separate hidden truth and system-visible observations. The default suite never calls a live model or MCP provider.

The ten required scenarios cover independent consensus, shared-source false consensus, an agent echo, stale lead-time contradiction, clean MCP evidence, genuine objective conflict, partial fulfillment, FEFO failure, warehouse capacity conflict, and duplicate execution retry.

Run the golden and generated-invariant layers independently:

```bash
uv run pytest tests/golden -q
uv run pytest tests/property -q
```

`evaluation.runner` executes the deterministic optimizer, independent verifier, lineage analysis, Jury policy, regret comparison, and execution-ledger simulation. `evaluation.metrics` reports solver, Jury, reason-code, lineage, regret, and retry results separately. Hidden claims and evidence are asserted to be disjoint from all visible fixtures.

## MCP product-interface evaluation

The inbound Codex-facing MCP facade must be evaluated separately from outbound procurement-provider contracts. Add contract and integration coverage for:

- strict intent-level tool schemas and bounded response sizes;
- organization and operator isolation;
- polling and resume without duplicate planning runs;
- immutable plan-hash approval challenges and expiry;
- rejection of model-supplied authority or changed plan content;
- duplicate `execute_approved_plan` calls returning the original audit result; and
- equivalence between MCP-triggered execution and the guarded application-service invariants.

Conversation quality may be evaluated for clarity, but prose must never determine solver correctness, Jury state, approval validity, or execution success.
