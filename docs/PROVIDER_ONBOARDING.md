# Operational MCP provider onboarding

Civitas can run a deterministic sandbox or route procurement capabilities to
one or more MCP servers owned by the user. Configuration and credentials stay
on the user's machine. Application and domain code never receive raw provider
credentials.

## Recommended local configuration

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and choose
an owner-controlled configuration directory:

```bash
uv tool install --from git+https://github.com/minnhaaaaa/civitas civitas
export CIVITAS_CONFIG_HOME="$PWD/.civitas"
export CIVITAS_PROVIDER_CONFIG="$CIVITAS_CONFIG_HOME/providers.json"
```

Register a local STDIO provider. Use an absolute command path when the command
is not on the system executable path:

```bash
civitas providers add operations \
  --name "Operations MCP" \
  --transport stdio \
  --command /absolute/path/to/operations-mcp \
  --planning-env OPERATIONS_TOKEN=OPERATIONS_PLANNING_TOKEN \
  --dissent-env OPERATIONS_TOKEN=OPERATIONS_DISSENT_TOKEN \
  --execution-env OPERATIONS_TOKEN=OPERATIONS_EXECUTION_TOKEN
```

The values after `=` are environment-variable names on the user's machine.
Secret values are never written to `providers.json`. Planning and Dissent
should use separate read-only credentials. Only execution should receive write
authority.

For a Streamable HTTP MCP, use HTTPS except for loopback development:

```bash
civitas providers add purchasing \
  --name "Purchasing MCP" \
  --transport http \
  --url https://purchasing.example.com/mcp \
  --planning-auth-env PURCHASING_READ_TOKEN \
  --dissent-auth-env PURCHASING_DISSENT_TOKEN \
  --execution-auth-env PURCHASING_WRITE_TOKEN
```

Map each Civitas capability to the tool that provides it. Capabilities may be
split across inventory, demand, supplier, logistics, and purchasing MCPs:

```bash
civitas providers map operations.stock_snapshot get_inventory
civitas providers map operations.demand_forecast get_demand
civitas providers map purchasing.offers get_supplier_offers
civitas providers map purchasing.lead_times get_lead_times
civitas providers map operations.warehouse_capacity get_warehouse_capacity
civitas providers map operations.transport_capacity get_transport_capacity
civitas providers map purchasing.create_order create_procurement_order
civitas providers enable operations
civitas providers enable purchasing
civitas providers use-live
civitas providers list
```

If the provider already uses Civitas field names, no mapping file is required.
Otherwise, pass `--mapping inventory.v1.json`. Mapping paths are resolved inside
the provider-config directory and cannot escape it. A version 1 mapping can
rename request fields and normalize one response collection:

```json
{
  "mapping_version": "1",
  "request": {
    "fields": {
      "site": "/warehouse_id"
    }
  },
  "response_collection": {
    "source_pointer": "/stockRows",
    "target_field": "lots",
    "fields": {
      "lot_id": "/batchId",
      "sku_id": "/productId",
      "warehouse_id": "/siteId",
      "quantity": "/available"
    },
    "constants": {
      "unit_of_measure": "each"
    }
  }
}
```

Set `CIVITAS_PROVIDER_CONFIG` for both `civitas-mcp` and `civitas-worker`.
Leave `CIVITAS_PROVIDER_FACTORY` unset because only one provider bootstrap may
be configured. Startup discovers every mapped tool and fails closed if a tool
is missing, its read/write mode changed, or the purchase-order tool does not
advertise idempotency.

Switch to the simulated workflow without deleting the live configuration:

```bash
civitas providers use-sandbox
# Run the local server and worker normally.
civitas providers use-live
```

## Required planning capabilities

A provider manifest must discover these read-only tools:

| Tool | Required typed payload |
| --- | --- |
| `get_inventory` | lot balances, SKU, warehouse, UOM and observation time |
| `get_demand` | demand by SKU, warehouse and planning bucket |
| `get_supplier_offers` | supplier/SKU/destination, available quantity and unit price |
| `get_lead_times` | supplier/SKU/destination and lead-time/delivery window |
| `get_warehouse_capacity` | warehouse/SKU capacity by bucket |
| `get_transport_capacity` | eligible lane capacity by bucket (an empty list is valid) |

Planning and clean-room Dissent credentials must be read-only and isolated from
one another. Only the execution connection may discover
`create_procurement_order`; that write must support an idempotency key.

## Advanced factory contract

Implement an async or synchronous callable receiving `RuntimeSettings` and
returning `ProviderRuntimeDependencies`:

```python
async def create_dependencies(settings: RuntimeSettings) -> ProviderRuntimeDependencies:
    connections = await your_onboarder.connect(your_registration)
    return ProviderRuntimeDependencies(
        planning=ProviderPlanningRuntime.from_connections(connections),
        execution=ProviderExecutionRuntime(
            reads=connections.evidence,
            connections=your_execution_connection_factory,
            server_name=connections.evidence.manifest.server_name,
        ),
    )
```

Configure `CIVITAS_PROVIDER_FACTORY=your_package.bootstrap:create_dependencies`
for both the MCP server and worker. Production startup fails closed if the
factory is absent, cannot be imported, violates capability policy, or cannot
isolate planning, Dissent, and execution credentials.

Use the factory only for custom adapters that cannot be represented by local
STDIO or Streamable HTTP configuration. Do not set it together with
`CIVITAS_PROVIDER_CONFIG`.

The execution client is created from the persisted approval receipt and exact
selected-plan hash. Provider writes cannot be reached from planning, Dissent,
Codex, the viewer, or a generic repository API.
