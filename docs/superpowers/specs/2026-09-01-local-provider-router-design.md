# Local MCP Provider Router Design

Date: 2026-09-01
Status: Approved

## Summary

Civitas will let operators connect organization-owned MCP servers for inventory,
demand, supplier, warehouse, transport, and purchase-order operations. All
configuration, credentials, and provider traffic remain on the operator's machine
or self-hosted Civitas deployment. The public website only explains the setup and
generates local commands. It never receives provider configuration or secrets.

The existing deterministic provider remains available as a clearly marked
simulation. Real and simulated providers use the same canonical capability
boundary, evidence pipeline, Jury checks, approval flow, and execution ledger.

## Goals

- Support multiple STDIO and Streamable HTTP MCP servers in one planning run.
- Let one MCP provide every capability or combine specialized MCP servers.
- Provide explicit local CLI configuration instead of importing Codex or Claude
  configuration.
- Normalize arbitrary provider tool names and payloads into Civitas's typed
  operational contracts without executing user-authored code.
- Keep planning, clean-room Dissent, and execution access isolated.
- Keep purchase-order writes behind freshness validation, approval binding, and
  idempotency protection.
- Preserve a zero-configuration simulated demonstration.
- Explain both modes concisely on the landing page.

## Non-goals

- A hosted multi-tenant credential vault or provider-control plane.
- Uploading provider credentials or configuration to the Vercel frontend.
- Importing or modifying another MCP client's configuration files.
- Generic passthrough access to arbitrary MCP tools.
- Arbitrary Python, JavaScript, shell, or template expressions in mappings.
- Executing purchase orders from the browser.

## Architecture

```text
Codex / Claude / MCP client
          |
          | inbound intent-level MCP
          v
      Civitas MCP
          |
          v
  Local capability router
      |       |       |
      v       v       v
 Inventory  Supplier  Purchasing
    MCP       MCP        MCP
```

The installed Civitas process owns the capability router. The router loads one
versioned local configuration, connects only to explicitly registered providers,
and exposes canonical capabilities to application services. Domain, Parliament,
Jury, and solver code never see transport-specific configuration or credentials.

The router extends the existing `ProviderOnboarder`, provider contracts, and
planning/execution ports. It does not add a second execution path.

## Canonical capabilities

Version 1 defines these allowlisted capabilities:

| Capability | Mode | Required use |
| --- | --- | --- |
| `get_inventory` | read | Lot balances, state, expiry, warehouse, and observation time |
| `get_demand` | read | Demand by SKU, warehouse, and planning bucket |
| `get_supplier_offers` | read | Supplier availability, price, quantity, and validity |
| `get_lead_times` | read | Lead time and delivery-window evidence |
| `get_warehouse_capacity` | read | Capacity by warehouse and bucket |
| `get_transport_capacity` | read | Eligible lane capacity by bucket |
| `create_procurement_order` | write | Approved and idempotent purchase-order creation |

Exactly one active binding exists for each required canonical capability. A
single provider may own several bindings. Different providers may own different
bindings. Missing or ambiguous required bindings fail closed.

## Local configuration

The CLI owns a versioned configuration file in the platform-appropriate user
configuration directory. Writes are atomic and the file is readable only by its
owner. The file contains no credential values.

Provider transports use a discriminated contract:

- STDIO stores an executable and argument array. The router invokes the
  executable directly and never uses a shell.
- Streamable HTTP stores an absolute MCP endpoint. HTTPS is required except for
  loopback HTTP explicitly allowed in development mode.

Credential fields contain references only. Version 1 supports environment
variable references and an optional operating-system keychain resolver. Each
provider can define separate planning, Dissent, and execution profiles. Real
execution requires a distinct execution credential reference. Planning and
Dissent are read-only and cannot resolve execution credentials.

Representative commands:

```bash
civitas providers add warehouse \
  --transport stdio \
  --command uvx \
  --arg warehouse-mcp

civitas providers add purchasing \
  --transport http \
  --url https://mcp.company.internal/mcp \
  --execution-auth-env PURCHASING_MCP_TOKEN

civitas providers map warehouse.get_stock get_inventory \
  --mapping warehouse-inventory.v1.json
civitas providers map purchasing.create_order create_procurement_order \
  --mapping purchase-order.v1.json
civitas providers test
```

Arguments are individual CLI tokens. A quoted shell command is not accepted as
an executable configuration.

## Declarative mappings

A capability binding contains the provider ID, provider tool name, canonical
capability, mapping version, request mapping, and response mapping.

Mappings use a constrained data language:

- JSON Pointer reads from provider inputs and outputs.
- Named Civitas context values supply organization, planning-run, SKU,
  warehouse, supplier, horizon, approval, and idempotency fields.
- Constants are limited to JSON scalar values.
- Unit conversions come from an allowlisted conversion table with explicit
  source and destination units.
- Collection mappings declare the array pointer and canonical field pointers.

Mappings cannot invoke functions, access files, read environment variables,
perform network calls, interpolate shell text, or evaluate expressions. Required
canonical identifiers, quantities, units, timestamps, and validity data must
validate before observations enter the solver. Raw provider payloads remain
lineage evidence but unmapped fields cannot influence plan construction.

Write mappings may map only the approved procurement-line contract plus
Civitas-owned execution metadata. They cannot add arbitrary operator input at
execution time.

## Onboarding and lifecycle

1. `providers add` validates and stores a disabled provider definition.
2. Civitas opens the selected transport and performs MCP initialization.
3. Civitas discovers the provider tool catalog and schemas.
4. `providers map` validates a binding against the discovered tool schema and
   the canonical capability schema.
5. `providers test` performs read-only test calls, validates mapped results, and
   reports missing capabilities or schema errors without exposing secrets.
6. The operator explicitly enables the validated provider and bindings.
7. Startup revalidates configuration structure and tool schemas. A changed schema
   quarantines the affected binding until it is tested and enabled again.

Configuration changes are local administrative actions and are recorded in a
redacted audit log. The audit record contains provider ID, capability, schema
fingerprint, actor, time, and outcome, but never endpoint credentials or raw
authorization headers.

## Planning and Dissent flow

At the start of a planning run, the router freezes the active provider-binding
version into the run metadata. Required reads execute concurrently with bounded
timeouts. Results pass through the declarative mapper, provider response
validation, evidence recording, and the existing planning-input assembler.

Dissent gets a new transport session, separate credential profile, separate MCP
cache namespace, and the read-only subset of the frozen bindings. A second call
to the same canonical upstream data source improves freshness but does not count
as independent evidence.

If a required read is unavailable, invalid, or unmapped, the run returns
`CONNECTION_REQUIRED` or `INVESTIGATE` with stable reason codes. Civitas does not
substitute fabricated data or silently fall back to simulation.

## Guarded execution flow

The execution router is constructed only after Civitas loads a valid approval
receipt and verifies the selected-plan hash. Before each write, Civitas refreshes
all mutable inputs using the frozen bindings and reruns deterministic feasibility
and Jury gates.

`create_procurement_order` receives only:

- the solver-validated procurement lines;
- execution ID;
- approval receipt ID;
- selected-plan hash; and
- a stable, provider-specific idempotency key.

The local execution ledger claims the idempotency key atomically before the
provider call. A repeated key with a different request hash is rejected. A
successful response is persisted with the provider reference. A timeout or lost
response becomes `UNKNOWN` and requires reconciliation against the provider. It
is not retried as a new purchase.

## Simulation

`civitas providers use-sandbox` activates the deterministic local provider for
all canonical capabilities. No network connection or credentials are required.
Sandbox state, evidence, plans, approvals, execution receipts, and frontend copy
are marked `SIMULATED` and `NO REAL PURCHASE`.

Switching between real and simulated mode is explicit. Civitas never falls back
from a failed real provider to the sandbox within an existing planning run.

## Security model

Trust boundaries are the local CLI input, configuration file, STDIO child
processes, remote MCP endpoints, MCP responses, model outputs, and purchase-order
writes.

Controls include:

- no credential values in configuration, logs, evidence, prompts, MCP arguments,
  errors, or browser state;
- direct process spawning with executable and argument arrays and no shell;
- explicit operator approval before registering an executable or endpoint;
- HTTPS by default and bounded connection, discovery, and invocation timeouts;
- redirect rejection for HTTP MCP endpoints;
- strict response size, collection size, and numeric bounds;
- typed validation of all provider responses and model outputs;
- least-privilege capability allowlists per context;
- immutable configuration fingerprints on planning runs and approvals;
- no write capability in planning or Dissent sessions;
- local and provider-side idempotency requirements for purchase-order writes;
- secret redaction tests covering success and error paths.

Because a configured STDIO MCP server is executable code, Civitas displays that
risk before enabling it. Registration is an explicit local administrative action.

## Frontend changes

The landing page remains static and never submits configuration. It will:

- remove every em dash from visible frontend copy;
- reduce hero, feature, Parliament, Jury, and installation text;
- add a compact `Connect your systems` diagram showing several MCP providers
  routed through Civitas;
- provide `Use sandbox` and `Connect my MCPs` terminal tabs;
- show the shortest useful explicit configuration sequence;
- clearly distinguish simulated operation from approval-gated real execution.

An automated source scan rejects em dashes in frontend copy. Browser verification
covers mobile and desktop layouts, keyboard navigation, reduced motion, terminal
tab switching, copy actions, and overflow.

## Error semantics

Public errors use the existing typed product error envelope. Provider failures
add stable reason codes without exposing transport internals:

- `provider_configuration_invalid`
- `provider_authorization_required`
- `provider_unavailable`
- `provider_schema_changed`
- `capability_binding_missing`
- `provider_response_invalid`
- `provider_write_unknown`
- `provider_reconciliation_required`

Internal exception types and secrets are never returned. Read failures may be
retried only under the bounded read policy. Writes are retried only when both
Civitas and the provider honor the same stable idempotency key.

## Testing strategy

- Contract tests for configuration, transport variants, mapping schemas, and
  stable errors.
- Unit and property tests for mapping totality, bounds, unit conversion,
  redaction, capability isolation, configuration fingerprints, and idempotency.
- Fake STDIO and Streamable HTTP MCP servers for transport contract tests.
- Integration tests combining inventory, supplier, warehouse, and purchasing
  tools from different providers.
- Adversarial tests for shell metacharacters, secret leakage, oversized payloads,
  malformed timestamps, unknown units, schema drift, redirects, private
  execution-tool access, and reused idempotency keys with changed payloads.
- End-to-end tests for a simulated run and an approval-gated real-provider run.
- Existing full Python suite, Ruff, mypy, pnpm audit, pip-audit, frontend lint,
  typecheck, production build, and responsive browser checks.

## Delivery slices

1. Versioned local configuration and canonical binding contracts.
2. STDIO and Streamable HTTP transports with capability discovery.
3. Declarative request and response mapping.
4. Multi-provider planning and clean-room Dissent routing.
5. Approval-bound purchasing and reconciliation behavior.
6. Sandbox parity and local CLI workflows.
7. Concise frontend provider setup experience.
8. Security audit, full verification, release documentation, and deployment.

Each slice remains fail-closed and testable. Incomplete real-provider support is
not exposed as production-ready.
