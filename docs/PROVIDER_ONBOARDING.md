# Operational MCP provider onboarding

Civitas ships a deterministic made-up provider for demos and a provider-neutral
factory boundary for organization-owned MCP servers. Application and domain code
never receive raw provider credentials.

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

## Factory contract

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

The execution client is created from the persisted approval receipt and exact
selected-plan hash. Provider writes cannot be reached from planning, Dissent,
Codex, the viewer, or a generic repository API.
