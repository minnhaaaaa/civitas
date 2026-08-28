"""Deterministic provider factory for local MCP deployment and black-box evaluation."""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import SecretStr

from civitas.application.live_execution import OnboardedExecutionConnectionFactory
from civitas.contracts.providers import (
    ProviderAccessContext,
    ProviderCapabilityManifest,
    ProviderRegistration,
    ProviderToolCapability,
)
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.integrations.mcp import clean_room_namespace
from civitas.integrations.providers import (
    ExecutionProviderContext,
    InMemoryCredentialResolver,
    ProviderOnboarder,
)
from civitas.ports.providers import ProviderCredential
from civitas.runtime.bootstrap import ProviderRuntimeDependencies
from civitas.runtime.composition import ProviderExecutionRuntime, ProviderPlanningRuntime
from civitas.runtime.config import RuntimeSettings, SettingsError


@dataclass(slots=True)
class SimulatedProcurementTransport:
    """In-process operational MCP simulator; never permitted in production."""

    sku_id: str
    warehouse_id: str
    supplier_id: str
    observation_version: str = "simulator-v1"
    _write_results: dict[str, MCPToolResult] = field(default_factory=dict)

    async def discover_capabilities(self) -> ProviderCapabilityManifest:
        reads = (
            "get_inventory",
            "get_demand",
            "get_supplier_offers",
            "get_lead_times",
            "get_warehouse_capacity",
            "get_transport_capacity",
        )
        return ProviderCapabilityManifest(
            provider_id="civitas-simulator",
            server_name="civitas-simulator",
            protocol_version="2026-08-01",
            discovered_at=datetime.now(UTC),
            tools=(
                *(
                    ProviderToolCapability(name=name, access_mode=MCPAccessMode.READ)
                    for name in reads
                ),
                ProviderToolCapability(
                    name="create_procurement_order",
                    access_mode=MCPAccessMode.WRITE,
                    idempotent=True,
                ),
            ),
            canonical_source_groups={name: f"simulator-dataset:{name}" for name in reads},
        )

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        now = datetime.now(UTC)
        payloads: dict[str, dict[str, object]] = {
            "get_inventory": {"lots": []},
            "get_demand": {
                "demands": [
                    {
                        "demand_id": "demand-1",
                        "sku_id": self.sku_id,
                        "warehouse_id": self.warehouse_id,
                        "bucket_id": "bucket-0",
                        "quantity": 10,
                        "unit_of_measure": "each",
                        "priority": 1,
                    }
                ]
            },
            "get_supplier_offers": {
                "offers": [
                    {
                        "offer_id": "offer-1",
                        "supplier_id": self.supplier_id,
                        "sku_id": self.sku_id,
                        "destination_warehouse_id": self.warehouse_id,
                        "arrival_bucket_id": "bucket-0",
                        "capacity": 100,
                        "available_quantity": 100,
                        "unit_cost": 5,
                        "unit_price": 5,
                        "currency": "USD",
                        "unit_of_measure": "each",
                    }
                ]
            },
            "get_lead_times": {
                "records": [
                    {
                        "supplier_id": self.supplier_id,
                        "sku_id": self.sku_id,
                        "destination_warehouse_id": self.warehouse_id,
                        "lead_time_days": 0,
                    }
                ]
            },
            "get_warehouse_capacity": {
                "records": [
                    {
                        "warehouse_id": self.warehouse_id,
                        "sku_id": self.sku_id,
                        "bucket_id": "bucket-0",
                        "remaining_capacity_units": 100,
                        "maximum_base_units": 100,
                        "available_quantity": 100,
                        "unit_of_measure": "each",
                    }
                ]
            },
            "get_transport_capacity": {"records": []},
        }
        if call.tool_name in payloads:
            return MCPToolResult(
                call_id=call.call_id,
                succeeded=True,
                observed_at=now,
                payload={
                    **deepcopy(payloads[call.tool_name]),
                    "observation_version": self.observation_version,
                    "source_id": f"simulator:{call.tool_name}",
                },
            )
        if call.tool_name != "create_procurement_order":
            return MCPToolResult(
                call_id=call.call_id,
                succeeded=False,
                observed_at=now,
                payload={},
                error_code="unknown_tool",
                error_message="unsupported simulator tool",
            )
        if call.access_mode is not MCPAccessMode.WRITE or not call.idempotency_key:
            return MCPToolResult(
                call_id=call.call_id,
                succeeded=False,
                observed_at=now,
                payload={},
                error_code="missing_write_authority",
                error_message="simulator writes require write mode and idempotency",
            )
        existing = self._write_results.get(call.idempotency_key)
        if existing is not None:
            return existing
        result = MCPToolResult(
            call_id=call.call_id,
            succeeded=True,
            observed_at=now,
            payload={
                "order_id": f"simulated-po:{call.idempotency_key}",
                "status": "created",
                "observation_version": self.observation_version,
            },
        )
        self._write_results[call.idempotency_key] = result
        return result


class _TransportFactory:
    def __init__(self, transport: SimulatedProcurementTransport) -> None:
        self._transport = transport

    async def connect(
        self,
        *,
        registration: ProviderRegistration,
        credential: ProviderCredential,
        context: ProviderAccessContext,
    ) -> SimulatedProcurementTransport:
        del registration, credential, context
        return self._transport


async def create_dependencies(settings: RuntimeSettings) -> ProviderRuntimeDependencies:
    """Create the complete local simulator boundary for server and worker processes."""

    if settings.environment == "production":
        raise SettingsError("the deterministic simulated provider is forbidden in production")
    transport = SimulatedProcurementTransport(
        sku_id=os.getenv("CIVITAS_SIMULATOR_SKU_ID", "sku-local"),
        warehouse_id=os.getenv("CIVITAS_SIMULATOR_WAREHOUSE_ID", "warehouse-local"),
        supplier_id=os.getenv("CIVITAS_SIMULATOR_SUPPLIER_ID", "supplier-local"),
    )
    registration = ProviderRegistration(
        provider_id="civitas-simulator",
        server_name="civitas-simulator",
        endpoint="inprocess://civitas-simulator",
        credential_refs={
            ProviderAccessContext.PLANNING: "simulator/planning",
            ProviderAccessContext.DISSENT: "simulator/dissent",
            ProviderAccessContext.EXECUTION: "simulator/execution",
        },
    )
    onboarder = ProviderOnboarder(
        credentials=InMemoryCredentialResolver(
            {
                "simulator/planning": SecretStr("simulator-planning-only"),
                "simulator/dissent": SecretStr("simulator-dissent-read-only"),
                "simulator/execution": SecretStr("simulator-execution-only"),
            }
        ),
        transports=_TransportFactory(transport),
    )
    connections = await onboarder.connect(
        registration=registration,
        namespace=clean_room_namespace("simulator-dissent"),
        execution_context=ExecutionProviderContext(
            execution_id="simulator-bootstrap",
            approval_receipt_id="simulator-bootstrap",
            approved_plan_hash="0" * 64,
        ),
    )
    return ProviderRuntimeDependencies(
        planning=ProviderPlanningRuntime.from_connections(connections),
        execution=ProviderExecutionRuntime(
            reads=connections.evidence,
            connections=OnboardedExecutionConnectionFactory(
                onboarder=onboarder,
                registration=registration,
            ),
            server_name=registration.server_name,
        ),
    )
