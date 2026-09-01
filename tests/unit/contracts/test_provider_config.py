"""Contracts for local, multi-provider MCP configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from civitas.contracts.provider_config import (
    CanonicalCapability,
    CapabilityBinding,
    HttpMCPTransport,
    LocalProviderConfiguration,
    ProviderDefinition,
    ProviderMode,
    StdioMCPTransport,
)


def test_stdio_provider_keeps_executable_arguments_and_secret_references_separate() -> None:
    provider = ProviderDefinition(
        provider_id="warehouse",
        display_name="Warehouse MCP",
        transport=StdioMCPTransport(
            command="uvx",
            args=("warehouse-mcp", "--tenant", "north"),
            credential_env_refs={
                "planning": {"WAREHOUSE_TOKEN": "WAREHOUSE_PLANNING_TOKEN"},
                "dissent": {"WAREHOUSE_TOKEN": "WAREHOUSE_DISSENT_TOKEN"},
            },
        ),
    )

    assert provider.transport.command == "uvx"
    assert provider.transport.args == ("warehouse-mcp", "--tenant", "north")
    serialized = provider.model_dump_json()
    assert "WAREHOUSE_PLANNING_TOKEN" in serialized
    assert "secret-value" not in serialized


def test_http_provider_rejects_insecure_non_loopback_endpoint() -> None:
    with pytest.raises(ValidationError, match="HTTPS or loopback HTTP"):
        HttpMCPTransport(url="http://inventory.example.com/mcp")

    transport = HttpMCPTransport(url="http://127.0.0.1:9000/mcp")

    assert str(transport.url) == "http://127.0.0.1:9000/mcp"


def test_configuration_rejects_duplicate_capability_bindings() -> None:
    provider = ProviderDefinition(
        provider_id="warehouse",
        display_name="Warehouse MCP",
        transport=StdioMCPTransport(command="uvx", args=("warehouse-mcp",)),
    )
    binding = CapabilityBinding(
        canonical_capability=CanonicalCapability.GET_INVENTORY,
        provider_id="warehouse",
        tool_name="get_stock",
    )

    with pytest.raises(ValidationError, match="one active binding"):
        LocalProviderConfiguration(
            mode=ProviderMode.LIVE,
            providers=(provider,),
            bindings=(binding, binding),
        )


def test_configuration_rejects_binding_to_unknown_provider() -> None:
    with pytest.raises(ValidationError, match="unknown provider"):
        LocalProviderConfiguration(
            mode=ProviderMode.LIVE,
            bindings=(
                CapabilityBinding(
                    canonical_capability=CanonicalCapability.GET_DEMAND,
                    provider_id="missing",
                    tool_name="forecast",
                ),
            ),
        )


def test_sandbox_configuration_requires_no_real_providers() -> None:
    configuration = LocalProviderConfiguration(mode=ProviderMode.SANDBOX)

    assert configuration.providers == ()
    assert configuration.bindings == ()
    assert configuration.config_version == "1"


def test_configuration_file_path_is_not_part_of_the_contract(tmp_path: Path) -> None:
    configuration = LocalProviderConfiguration(mode=ProviderMode.SANDBOX)

    assert str(tmp_path) not in configuration.model_dump_json()
