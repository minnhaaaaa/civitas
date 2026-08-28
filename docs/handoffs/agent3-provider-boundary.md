# Agent 3 handoff — outbound provider boundary

Branch: `live/provider-boundary`

## Delivered

- Provider-neutral onboarding contracts and ports for capability discovery,
  transport construction, and secret resolution.
- Separate planning, clean-room Dissent, and guarded-execution credential
  contexts. Registrations persist credential references only and reject reuse
  across contexts.
- Read-only planning and Dissent clients plus an explicit execution-only client
  and policy for provider writes.
- Bounded timeouts and retries. Reads and idempotency-key-protected writes may
  retry; an unkeyed write is never retried and is rejected by the MCP client.
- Strict typed operational observations paired with raw provider evidence and
  canonical-source lineage.
- Provider capability onboarding checks and an offline simulator manifest.
- Contract tests for discovery, malformed payload fail-closed behavior,
  credential isolation, Dissent write denial, execution writes, and retries.

## Integration notes

- Compose real transports by implementing `ProviderCredentialResolver` and
  `ProviderTransportFactory`; secret material is passed only between those two
  ports and must not be copied into MCP arguments, evidence, exceptions, or
  logs.
- Use `ProviderOnboarder.onboard()` before enabling a provider, then
  `ProviderOnboarder.connect()` to construct its three isolated clients.
- Inject `connections.evidence` into planning/investigation reads,
  `connections.dissent` into clean-room Dissent, and
  `connections.execution` only into the guarded execution service.
- Agent 4 binding is integrated on `live/approval-execution`: its product
  adapter derives `ExecutionProviderContext` from the persisted receipt and
  exact selected-plan hash, while `ContextBoundExecutionMCPClient` injects
  protected binding metadata into every write. This does not create a second
  approval or execution path.
- The mock provider remains deterministic and offline; its advertised manifest
  is the provider contract fixture for CI.
