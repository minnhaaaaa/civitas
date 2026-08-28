from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError
from tools.mock_mcp import MockProcurementMCPServer

from civitas.contracts.providers import (
    ProviderAccessContext,
    ProviderCapabilityManifest,
    ProviderRegistration,
    ProviderToolCapability,
)
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.integrations import (
    ExecutionProviderContext,
    InMemoryCredentialResolver,
    MCPAccessError,
    MCPInvocationError,
    ProviderOnboarder,
    ProviderRetryPolicy,
    ResilientProviderTransport,
    clean_room_namespace,
)
from civitas.ports.providers import ProviderCredential


def registration() -> ProviderRegistration:
    return ProviderRegistration(
        provider_id="mock-procurement",
        server_name="mock-procurement",
        endpoint="https://provider.invalid/mcp",
        credential_refs={
            ProviderAccessContext.PLANNING: "secret/planning",
            ProviderAccessContext.DISSENT: "secret/dissent",
            ProviderAccessContext.EXECUTION: "secret/execution",
        },
    )


class RecordingFactory:
    def __init__(self, transport: MockProcurementMCPServer) -> None:
        self.transport = transport
        self.contexts: list[ProviderAccessContext] = []
        self.credentials: list[ProviderCredential] = []

    async def connect(
        self,
        *,
        registration: ProviderRegistration,
        credential: ProviderCredential,
        context: ProviderAccessContext,
    ) -> MockProcurementMCPServer:
        del registration
        self.contexts.append(context)
        self.credentials.append(credential)
        return self.transport


def onboarder(
    server: MockProcurementMCPServer,
) -> tuple[ProviderOnboarder, RecordingFactory]:
    factory = RecordingFactory(server)
    resolver = InMemoryCredentialResolver(
        {
            "secret/planning": SecretStr("planning-token"),
            "secret/dissent": SecretStr("dissent-read-only-token"),
            "secret/execution": SecretStr("execution-token"),
        }
    )
    return ProviderOnboarder(credentials=resolver, transports=factory), factory


class CapturingServer(MockProcurementMCPServer):
    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.calls: list[MCPToolCall] = []

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        self.calls.append(call)
        return await super().invoke(call)


def read_call(tool: str) -> MCPToolCall:
    return MCPToolCall(
        call_id=f"call-{tool}",
        server_name="mock-procurement",
        tool_name=tool,
        arguments={"organization_id": "org-1"},
        access_mode=MCPAccessMode.READ,
    )


def write_call() -> MCPToolCall:
    return MCPToolCall(
        call_id="call-write",
        server_name="mock-procurement",
        tool_name="create_procurement_order",
        arguments={"lines": [{"sku_id": "sku-1", "quantity": "2"}]},
        access_mode=MCPAccessMode.WRITE,
        idempotency_key="execution-1:supplier-1",
    )


def test_registration_requires_separate_dissent_and_execution_credentials() -> None:
    with pytest.raises(ValidationError, match="distinct credential references"):
        ProviderRegistration(
            provider_id="provider",
            server_name="provider",
            endpoint="https://provider.invalid/mcp",
            credential_refs={context: "same-secret" for context in ProviderAccessContext},
        )


@pytest.mark.asyncio
async def test_onboarding_discovers_and_validates_simulator_contract() -> None:
    provider, _ = onboarder(MockProcurementMCPServer())

    report = await provider.onboard(registration())

    assert report.accepted is True
    assert report.manifest is not None
    assert {tool.name for tool in report.manifest.tools} >= {
        "get_inventory",
        "get_demand",
        "create_procurement_order",
    }


@pytest.mark.asyncio
async def test_connections_isolate_credentials_and_dissent_is_read_only() -> None:
    server = CapturingServer(
        inventory=[
            {
                "lot_id": "lot-1",
                "sku_id": "sku-1",
                "warehouse_id": "warehouse-1",
                "available_quantity": "12.5",
                "unit_of_measure": "kg",
            }
        ]
    )
    provider, factory = onboarder(server)
    connections = await provider.connect(
        registration=registration(),
        namespace=clean_room_namespace("dissent-run-1"),
        execution_context=ExecutionProviderContext(
            execution_id="execution-1",
            approval_receipt_id="receipt-1",
            approved_plan_hash="a" * 64,
        ),
    )

    read = await connections.evidence.read(
        call=read_call("get_inventory"), evidence_id="evidence-1", agent_id="planner"
    )

    assert read.observations[0].predicate == "inventory_balance"
    assert read.observations[0].value == 12.5
    assert read.evidence.identity.canonical_source_id == "mock-dataset:get_inventory"
    assert set(factory.contexts) == set(ProviderAccessContext)
    assert {credential.context for credential in factory.credentials} == set(ProviderAccessContext)
    assert "planning-token" not in repr(factory.credentials)
    assert "dissent-read-only-token" not in repr(factory.credentials)
    assert "execution-token" not in repr(factory.credentials)

    with pytest.raises(MCPAccessError, match="write access denied"):
        await connections.dissent.invoke(write_call())

    result = await connections.execution.invoke(write_call())
    assert result.payload["status"] == "created"
    provider_write = next(call for call in reversed(server.calls) if call.access_mode == "write")
    assert provider_write.arguments["_civitas_execution"] == {
        "execution_id": "execution-1",
        "approval_receipt_id": "receipt-1",
        "selected_plan_hash": "a" * 64,
    }

    with pytest.raises(MCPAccessError, match="owned by Civitas"):
        await connections.execution.invoke(
            write_call().model_copy(
                update={"arguments": {"_civitas_execution": {"approval_receipt_id": "spoof"}}}
            )
        )


@pytest.mark.asyncio
async def test_typed_read_fails_closed_on_malformed_provider_records() -> None:
    provider, _ = onboarder(MockProcurementMCPServer(inventory=[{"lot_id": "lot-1"}]))
    connections = await provider.connect(
        registration=registration(),
        namespace=clean_room_namespace("dissent-run-2"),
        execution_context=ExecutionProviderContext("execution-2", "receipt-2", "b" * 64),
    )

    with pytest.raises(MCPInvocationError, match="invalid available_quantity"):
        await connections.evidence.read(
            call=read_call("get_inventory"), evidence_id="evidence-malformed"
        )


class FlakyTransport(MockProcurementMCPServer):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        self.attempts += 1
        if self.attempts < 3:
            return MCPToolResult(
                call_id=call.call_id,
                succeeded=False,
                observed_at=datetime(2026, 8, 28, tzinfo=UTC),
                payload={},
                error_code="temporarily_unavailable",
                error_message="retry later",
            )
        return await super().invoke(call)


@pytest.mark.asyncio
async def test_transient_reads_retry_with_a_strict_bound() -> None:
    transport = FlakyTransport()
    resilient = ResilientProviderTransport(
        transport,
        ProviderRetryPolicy(max_attempts=3, timeout_seconds=1, backoff_seconds=0),
    )

    result = await resilient.invoke(read_call("get_inventory"))

    assert result.succeeded is True
    assert transport.attempts == 3


@pytest.mark.asyncio
async def test_onboarding_rejects_missing_required_read_capabilities() -> None:
    class IncompleteServer(MockProcurementMCPServer):
        async def discover_capabilities(self) -> ProviderCapabilityManifest:
            return ProviderCapabilityManifest(
                provider_id=self.provider_id,
                server_name=self.server_name,
                protocol_version="2026-08-01",
                discovered_at=datetime(2026, 8, 28, tzinfo=UTC),
                tools=(
                    ProviderToolCapability(name="get_inventory", access_mode=MCPAccessMode.READ),
                ),
            )

    provider, _ = onboarder(IncompleteServer())

    report = await provider.onboard(registration())

    assert report.accepted is False
    assert report.errors[0].startswith("missing_read_capabilities:")
