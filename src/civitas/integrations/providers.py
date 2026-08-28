"""Safe composition and onboarding for outbound operational MCP providers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic import SecretStr

from civitas.contracts.common import JsonObject
from civitas.contracts.providers import (
    OperationalEvidenceKind,
    OperationalObservation,
    ProviderAccessContext,
    ProviderCapabilityManifest,
    ProviderEvidenceRead,
    ProviderOnboardingReport,
    ProviderRegistration,
)
from civitas.contracts.tools import MCPAccessMode, MCPToolCall, MCPToolResult
from civitas.integrations.mcp import (
    DEFAULT_EXECUTION_POLICY,
    DEFAULT_READ_POLICY,
    CleanRoomNamespace,
    DissentMCPClient,
    ExecutionMCPClient,
    MCPAccessError,
    MCPClient,
    MCPInvocationError,
    ToolEvidenceMapping,
    evidence_from_tool_result,
)
from civitas.ports.mcp import MCPPort
from civitas.ports.providers import (
    OperationalProviderTransport,
    ProviderCredential,
    ProviderCredentialResolver,
    ProviderTransportFactory,
)


@dataclass(frozen=True, slots=True)
class ProviderRetryPolicy:
    max_attempts: int = 3
    timeout_seconds: float = 10.0
    backoff_seconds: float = 0.05

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.timeout_seconds <= 0 or self.backoff_seconds < 0:
            raise ValueError("timeout must be positive and backoff cannot be negative")


@dataclass(frozen=True, slots=True)
class SecretProviderCredential:
    """Redacted credential material passed only to provider transport factories."""

    secret: SecretStr
    context: ProviderAccessContext


@dataclass(frozen=True, slots=True)
class ExecutionProviderContext:
    execution_id: str
    approval_receipt_id: str
    approved_plan_hash: str

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (self.execution_id, self.approval_receipt_id, self.approved_plan_hash)
        ):
            raise ValueError("execution writes require execution, receipt, and plan identities")


class ContextBoundExecutionMCPClient(MCPPort):
    """Attach immutable Civitas approval identity to every provider write."""

    _BINDING_ARGUMENT = "_civitas_execution"

    def __init__(
        self,
        *,
        client: ExecutionMCPClient,
        execution_context: ExecutionProviderContext,
    ) -> None:
        self._client = client
        self.execution_context = execution_context

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        if call.access_mode is not MCPAccessMode.WRITE:
            return await self._client.invoke(call)
        if self._BINDING_ARGUMENT in call.arguments:
            raise MCPAccessError("execution binding metadata is owned by Civitas")
        bound_call = call.model_copy(
            update={
                "arguments": {
                    **call.arguments,
                    self._BINDING_ARGUMENT: {
                        "execution_id": self.execution_context.execution_id,
                        "approval_receipt_id": self.execution_context.approval_receipt_id,
                        "selected_plan_hash": self.execution_context.approved_plan_hash,
                    },
                }
            }
        )
        return await self._client.invoke(bound_call)


class ResilientProviderTransport:
    """Applies bounded timeout/retry behavior without retrying unsafe writes."""

    _TRANSIENT_ERROR_CODES = frozenset(
        {"timeout", "temporarily_unavailable", "rate_limited", "transport_error"}
    )

    def __init__(
        self,
        transport: OperationalProviderTransport,
        policy: ProviderRetryPolicy | None = None,
    ) -> None:
        self._transport = transport
        self._policy = policy or ProviderRetryPolicy()

    async def discover_capabilities(self) -> ProviderCapabilityManifest:
        try:
            return await asyncio.wait_for(
                self._transport.discover_capabilities(), timeout=self._policy.timeout_seconds
            )
        except TimeoutError as error:
            raise MCPInvocationError("provider capability discovery timed out") from error

    async def invoke(self, call: MCPToolCall) -> MCPToolResult:
        retryable_write = (
            call.access_mode is MCPAccessMode.WRITE
            and call.idempotency_key is not None
            and bool(call.idempotency_key.strip())
        )
        attempts = (
            self._policy.max_attempts
            if (call.access_mode is MCPAccessMode.READ or retryable_write)
            else 1
        )
        last_error: BaseException | None = None
        for attempt in range(attempts):
            try:
                result = await asyncio.wait_for(
                    self._transport.invoke(call), timeout=self._policy.timeout_seconds
                )
                if result.succeeded or result.error_code not in self._TRANSIENT_ERROR_CODES:
                    return result
                last_error = MCPInvocationError(
                    result.error_message or f"transient provider error: {result.error_code}"
                )
            except (TimeoutError, ConnectionError, OSError) as error:
                last_error = error
            if attempt + 1 < attempts and self._policy.backoff_seconds:
                await asyncio.sleep(self._policy.backoff_seconds * (2**attempt))
        if isinstance(last_error, MCPInvocationError):
            raise last_error
        raise MCPInvocationError(
            f"provider call {call.tool_name} failed after {attempts} attempt(s)"
        ) from last_error


class ProviderEvidenceClient:
    """Read-only adapter that turns provider data into typed, lineage-ready evidence."""

    def __init__(
        self,
        *,
        client: MCPClient,
        manifest: ProviderCapabilityManifest,
    ) -> None:
        self._client = client
        self.manifest = manifest

    async def read(
        self,
        *,
        call: MCPToolCall,
        evidence_id: str,
        claim_ids: Sequence[str] = (),
        agent_id: str | None = None,
    ) -> ProviderEvidenceRead:
        if call.access_mode is not MCPAccessMode.READ:
            raise MCPAccessError("evidence retrieval is read-only")
        capability = next(
            (item for item in self.manifest.tools if item.name == call.tool_name), None
        )
        if capability is None or capability.access_mode is not MCPAccessMode.READ:
            raise MCPAccessError(f"provider did not advertise read capability {call.tool_name}")
        result = await self._client.invoke(call)
        source_group = self.manifest.canonical_source_groups.get(call.tool_name)
        evidence = evidence_from_tool_result(
            evidence_id=evidence_id,
            call=call,
            result=result,
            mapping=ToolEvidenceMapping(
                canonical_source_type=source_group or self.manifest.provider_id,
                canonical_source_id=source_group,
            ),
            claim_ids=claim_ids,
            agent_id=agent_id,
        )
        return ProviderEvidenceRead(
            call=call,
            result=result,
            evidence=evidence,
            observations=_parse_observations(call.tool_name, result),
        )


@dataclass(frozen=True, slots=True)
class ProviderConnections:
    evidence: ProviderEvidenceClient
    dissent: DissentMCPClient
    execution: ContextBoundExecutionMCPClient
    execution_context: ExecutionProviderContext


class ProviderOnboarder:
    """Validates and composes a provider without persisting credential material."""

    def __init__(
        self,
        *,
        credentials: ProviderCredentialResolver,
        transports: ProviderTransportFactory,
        retry_policy: ProviderRetryPolicy | None = None,
    ) -> None:
        self._credentials = credentials
        self._transports = transports
        self._retry_policy = retry_policy

    async def onboard(self, registration: ProviderRegistration) -> ProviderOnboardingReport:
        try:
            transport = await self._connect(registration, ProviderAccessContext.PLANNING)
            manifest = await transport.discover_capabilities()
        except Exception as error:
            return ProviderOnboardingReport(
                provider_id=registration.provider_id,
                accepted=False,
                errors=(f"capability_discovery_failed:{type(error).__name__}",),
            )
        errors = list(_manifest_errors(registration, manifest))
        return ProviderOnboardingReport(
            provider_id=registration.provider_id,
            accepted=not errors,
            manifest=manifest,
            errors=tuple(errors),
        )

    async def connect(
        self,
        *,
        registration: ProviderRegistration,
        namespace: CleanRoomNamespace,
        execution_context: ExecutionProviderContext,
    ) -> ProviderConnections:
        planning_transport, dissent_transport, execution_transport = await asyncio.gather(
            self._connect(registration, ProviderAccessContext.PLANNING),
            self._connect(registration, ProviderAccessContext.DISSENT),
            self._connect(registration, ProviderAccessContext.EXECUTION),
        )
        manifest = await planning_transport.discover_capabilities()
        errors = _manifest_errors(registration, manifest)
        if errors:
            raise MCPAccessError("provider failed onboarding: " + ", ".join(errors))
        evidence_client = ProviderEvidenceClient(
            client=MCPClient(transport=planning_transport, policy=DEFAULT_READ_POLICY),
            manifest=manifest,
        )
        return ProviderConnections(
            evidence=evidence_client,
            dissent=DissentMCPClient(transport=dissent_transport, namespace=namespace),
            execution=ContextBoundExecutionMCPClient(
                client=ExecutionMCPClient(
                    transport=execution_transport, policy=DEFAULT_EXECUTION_POLICY
                ),
                execution_context=execution_context,
            ),
            execution_context=execution_context,
        )

    async def _connect(
        self,
        registration: ProviderRegistration,
        context: ProviderAccessContext,
    ) -> ResilientProviderTransport:
        credential_ref = registration.credential_refs[context]
        credential = await self._credentials.resolve(
            provider_id=registration.provider_id,
            credential_ref=credential_ref,
            context=context,
        )
        transport = await self._transports.connect(
            registration=registration,
            credential=credential,
            context=context,
        )
        return ResilientProviderTransport(transport, self._retry_policy)


class InMemoryCredentialResolver:
    """Simulator/test resolver; secret values remain redacted and never enter calls."""

    def __init__(self, credentials: Mapping[str, SecretStr]) -> None:
        self._credentials = dict(credentials)

    async def resolve(
        self,
        *,
        provider_id: str,
        credential_ref: str,
        context: ProviderAccessContext,
    ) -> ProviderCredential:
        del provider_id
        try:
            return SecretProviderCredential(self._credentials[credential_ref], context)
        except KeyError as error:
            raise MCPAccessError("provider credential reference is unavailable") from error


def _manifest_errors(
    registration: ProviderRegistration, manifest: ProviderCapabilityManifest
) -> tuple[str, ...]:
    errors: list[str] = []
    if manifest.provider_id != registration.provider_id:
        errors.append("provider_id_mismatch")
    if manifest.server_name != registration.server_name:
        errors.append("server_name_mismatch")
    reads = {tool.name for tool in manifest.tools if tool.access_mode is MCPAccessMode.READ}
    missing = DEFAULT_READ_POLICY.read_tools - reads
    if missing:
        errors.append("missing_read_capabilities:" + ",".join(sorted(missing)))
    advertised_writes = {
        tool.name for tool in manifest.tools if tool.access_mode is MCPAccessMode.WRITE
    }
    unsupported_writes = advertised_writes - DEFAULT_EXECUTION_POLICY.write_tools
    if unsupported_writes:
        errors.append("unsupported_write_capabilities:" + ",".join(sorted(unsupported_writes)))
    return tuple(errors)


def _parse_observations(
    tool_name: str, result: MCPToolResult
) -> tuple[OperationalObservation, ...]:
    spec = _READ_SPECS.get(tool_name)
    if spec is None:
        raise MCPInvocationError(f"no typed evidence parser for {tool_name}")
    kind, collection_key, id_fields, value_field, unit_default, predicate = spec
    records = _records(result.payload, collection_key)
    observations: list[OperationalObservation] = []
    for record in records:
        subject_parts = [_required_text(record, field) for field in id_fields]
        observation_value = _required_scalar(record, value_field)
        unit_value = record.get("unit_of_measure", unit_default)
        if not isinstance(unit_value, str) or not unit_value.strip():
            raise MCPInvocationError(f"{tool_name} returned an invalid unit_of_measure")
        scope: JsonObject = {}
        for key in ("sku_id", "warehouse_id", "supplier_id", "source_warehouse_id"):
            scope_value = record.get(key)
            if isinstance(scope_value, str) and scope_value:
                scope[key] = scope_value
        observations.append(
            OperationalObservation(
                kind=kind,
                subject=":".join(subject_parts),
                predicate=predicate,
                value=observation_value,
                unit=unit_value,
                valid_at=result.observed_at,
                scope=scope,
            )
        )
    return tuple(observations)


def _records(payload: JsonObject, key: str) -> tuple[JsonObject, ...]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise MCPInvocationError(f"provider payload is missing {key}")
    if any(not isinstance(item, dict) for item in value):
        raise MCPInvocationError(f"provider payload {key} must contain objects")
    return tuple(item for item in value if isinstance(item, dict))


def _required_text(record: JsonObject, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MCPInvocationError(f"provider record is missing {field}")
    return value


def _required_scalar(record: JsonObject, field: str) -> Decimal | str | bool:
    value = record.get(field)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            if isinstance(value, str) and value.strip():
                return value
    raise MCPInvocationError(f"provider record is missing or has invalid {field}")


_ReadSpec = tuple[
    OperationalEvidenceKind,
    str,
    tuple[str, ...],
    str,
    str,
    str,
]

_READ_SPECS: dict[str, _ReadSpec] = {
    "get_inventory": (
        OperationalEvidenceKind.INVENTORY,
        "lots",
        ("lot_id",),
        "available_quantity",
        "unit",
        "inventory_balance",
    ),
    "get_demand": (
        OperationalEvidenceKind.DEMAND,
        "demands",
        ("sku_id", "warehouse_id"),
        "quantity",
        "unit",
        "demand_forecast",
    ),
    "get_supplier_offers": (
        OperationalEvidenceKind.SUPPLIER_OFFER,
        "offers",
        ("offer_id",),
        "unit_price",
        "currency",
        "unit_price",
    ),
    "get_lead_times": (
        OperationalEvidenceKind.LEAD_TIME,
        "records",
        ("supplier_id",),
        "lead_time_days",
        "day",
        "lead_time",
    ),
    "get_warehouse_capacity": (
        OperationalEvidenceKind.WAREHOUSE_CAPACITY,
        "records",
        ("warehouse_id",),
        "remaining_capacity_units",
        "unit",
        "warehouse_capacity",
    ),
    "get_transport_capacity": (
        OperationalEvidenceKind.TRANSPORT_CAPACITY,
        "records",
        ("source_warehouse_id", "destination_warehouse_id"),
        "available_quantity",
        "unit",
        "transport_capacity",
    ),
}
