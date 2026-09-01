"""Runtime composition for locally configured operational MCP providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from civitas.application.live_execution import ExecutionProviderConnectionFactory
from civitas.contracts.provider_config import (
    CanonicalCapability,
    CapabilityMapping,
    LocalProviderConfiguration,
    ProviderDefinition,
    ProviderMode,
)
from civitas.contracts.providers import ProviderAccessContext, ProviderCapabilityManifest
from civitas.integrations.local_mcp import LocalMCPTransport
from civitas.integrations.local_router import LocalCapabilityRouter
from civitas.integrations.mcp import (
    DEFAULT_EXECUTION_POLICY,
    DEFAULT_READ_POLICY,
    DissentMCPClient,
    ExecutionMCPClient,
    MCPClient,
    clean_room_namespace,
)
from civitas.integrations.provider_config import LocalProviderConfigStore, load_mappings
from civitas.integrations.providers import (
    ContextBoundExecutionMCPClient,
    ExecutionProviderContext,
    ProviderEvidenceClient,
)
from civitas.ports.mcp import MCPPort
from civitas.ports.providers import OperationalProviderTransport
from civitas.runtime.bootstrap import ProviderRuntimeDependencies
from civitas.runtime.composition import ProviderExecutionRuntime, ProviderPlanningRuntime
from civitas.runtime.config import RuntimeSettings, SettingsError

TransportBuilder = Callable[
    [ProviderDefinition, ProviderAccessContext, frozenset[str]],
    OperationalProviderTransport,
]

_REQUIRED_CAPABILITIES = frozenset(CanonicalCapability)
_WRITE_CAPABILITIES = frozenset({CanonicalCapability.CREATE_PROCUREMENT_ORDER})


async def create_dependencies(settings: RuntimeSettings) -> ProviderRuntimeDependencies:
    """Load a secret-free local provider file into the guarded runtime."""

    config_path = settings.provider_config_path
    if config_path is None:
        raise SettingsError("CIVITAS_PROVIDER_CONFIG is required")
    store = LocalProviderConfigStore(config_path)
    try:
        configuration = store.load()
        mappings = load_mappings(store, configuration)
    except (OSError, ValueError) as error:
        raise SettingsError("local provider configuration is invalid") from error
    if configuration.mode is ProviderMode.SANDBOX:
        from civitas.runtime.simulated_provider import create_dependencies as create_simulator

        return await create_simulator(settings)
    try:
        return await build_local_provider_dependencies(
            configuration=configuration,
            mappings=mappings,
        )
    except ValueError as error:
        raise SettingsError("local provider configuration is incomplete") from error


class LocalExecutionConnectionFactory(ExecutionProviderConnectionFactory):
    def __init__(
        self,
        *,
        configuration: LocalProviderConfiguration,
        mappings: Mapping[str, CapabilityMapping],
        transport_builder: TransportBuilder,
    ) -> None:
        self._configuration = configuration
        self._mappings = dict(mappings)
        self._transport_builder = transport_builder

    async def connect(self, context: ExecutionProviderContext) -> MCPPort:
        router = _router(
            configuration=self._configuration,
            mappings=self._mappings,
            context=ProviderAccessContext.EXECUTION,
            transport_builder=self._transport_builder,
        )
        await router.discover_capabilities()
        return ContextBoundExecutionMCPClient(
            client=ExecutionMCPClient(
                transport=router,
                policy=DEFAULT_EXECUTION_POLICY,
            ),
            execution_context=context,
        )


async def build_local_provider_dependencies(
    *,
    configuration: LocalProviderConfiguration,
    mappings: Mapping[str, CapabilityMapping],
    transport_builder: TransportBuilder | None = None,
) -> ProviderRuntimeDependencies:
    if configuration.mode is not ProviderMode.LIVE:
        raise ValueError("local live-provider composition requires live mode")
    _validate_required_bindings(configuration)
    builder = transport_builder or _default_transport_builder
    planning_router = _router(
        configuration=configuration,
        mappings=mappings,
        context=ProviderAccessContext.PLANNING,
        transport_builder=builder,
    )
    dissent_router = _router(
        configuration=configuration,
        mappings=mappings,
        context=ProviderAccessContext.DISSENT,
        transport_builder=builder,
    )
    planning_manifest, dissent_manifest = await _discover_pair(
        planning_router,
        dissent_router,
    )
    planning_client = MCPClient(transport=planning_router, policy=DEFAULT_READ_POLICY)
    planning_evidence = ProviderEvidenceClient(
        client=planning_client,
        manifest=planning_manifest,
    )
    namespace = clean_room_namespace("local-provider-dissent")
    dissent_client = DissentMCPClient(transport=dissent_router, namespace=namespace)
    dissent_evidence = ProviderEvidenceClient(
        client=dissent_client,
        manifest=dissent_manifest,
    )
    return ProviderRuntimeDependencies(
        planning=ProviderPlanningRuntime(
            evidence=planning_evidence,
            dissent=dissent_evidence,
            dissent_namespace=namespace,
            server_name=planning_manifest.server_name,
        ),
        execution=ProviderExecutionRuntime(
            reads=planning_evidence,
            connections=LocalExecutionConnectionFactory(
                configuration=configuration,
                mappings=mappings,
                transport_builder=builder,
            ),
            server_name=planning_manifest.server_name,
        ),
    )


async def _discover_pair(
    planning: LocalCapabilityRouter,
    dissent: LocalCapabilityRouter,
) -> tuple[ProviderCapabilityManifest, ProviderCapabilityManifest]:
    import asyncio

    planning_manifest, dissent_manifest = await asyncio.gather(
        planning.discover_capabilities(),
        dissent.discover_capabilities(),
    )
    return planning_manifest, dissent_manifest


def _router(
    *,
    configuration: LocalProviderConfiguration,
    mappings: Mapping[str, CapabilityMapping],
    context: ProviderAccessContext,
    transport_builder: TransportBuilder,
) -> LocalCapabilityRouter:
    transports: dict[str, OperationalProviderTransport] = {}
    for provider in configuration.providers:
        if not provider.enabled:
            continue
        write_tools = frozenset(
            binding.tool_name
            for binding in configuration.bindings
            if binding.provider_id == provider.provider_id
            and binding.enabled
            and binding.canonical_capability in _WRITE_CAPABILITIES
        )
        transports[provider.provider_id] = transport_builder(provider, context, write_tools)
    return LocalCapabilityRouter(
        configuration=configuration,
        context=context,
        transports=transports,
        mappings=mappings,
    )


def _default_transport_builder(
    provider: ProviderDefinition,
    context: ProviderAccessContext,
    write_tools: frozenset[str],
) -> OperationalProviderTransport:
    return LocalMCPTransport(
        provider=provider,
        context=context,
        write_tool_names=write_tools,
    )


def _validate_required_bindings(configuration: LocalProviderConfiguration) -> None:
    enabled_providers = {
        provider.provider_id for provider in configuration.providers if provider.enabled
    }
    available = {
        binding.canonical_capability
        for binding in configuration.bindings
        if binding.enabled and binding.provider_id in enabled_providers
    }
    missing = sorted(capability.value for capability in _REQUIRED_CAPABILITIES - available)
    if missing:
        raise ValueError("missing required capabilities: " + ", ".join(missing))
