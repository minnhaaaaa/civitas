"""Persistence behavior for the local provider configuration file."""

from pathlib import Path

import pytest

from civitas.contracts.provider_config import (
    CanonicalCapability,
    CapabilityBinding,
    LocalProviderConfiguration,
    ProviderDefinition,
    ProviderMode,
    StdioMCPTransport,
)
from civitas.integrations.mcp import MCPAccessError
from civitas.integrations.provider_config import LocalProviderConfigStore, load_mappings


def test_store_round_trips_configuration_with_owner_only_permissions(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "providers.json"
    store = LocalProviderConfigStore(path)
    expected = LocalProviderConfiguration(mode=ProviderMode.SANDBOX)

    store.save(expected)

    assert store.load() == expected
    assert path.stat().st_mode & 0o777 == 0o600
    assert not tuple(path.parent.glob("*.tmp"))


def test_missing_configuration_loads_disabled_live_mode(tmp_path: Path) -> None:
    store = LocalProviderConfigStore(tmp_path / "providers.json")

    configuration = store.load()

    assert configuration.mode is ProviderMode.LIVE
    assert configuration.providers == ()


def test_mapping_files_load_relative_to_configuration_directory(tmp_path: Path) -> None:
    store = LocalProviderConfigStore(tmp_path / "providers.json")
    mapping_path = tmp_path / "inventory.v1.json"
    mapping_path.write_text(
        '{"mapping_version":"1","request":{"fields":{"site":"/warehouse_id"}}}',
        encoding="utf-8",
    )
    configuration = LocalProviderConfiguration(
        providers=(
            ProviderDefinition(
                provider_id="warehouse",
                display_name="Warehouse",
                transport=StdioMCPTransport(command="warehouse-mcp"),
            ),
        ),
        bindings=(
            CapabilityBinding(
                canonical_capability=CanonicalCapability.GET_INVENTORY,
                provider_id="warehouse",
                tool_name="get_stock",
                mapping_file="inventory.v1.json",
            ),
        ),
    )

    mappings = load_mappings(store, configuration)

    assert mappings["inventory.v1.json"].request.fields == {"site": "/warehouse_id"}


def test_mapping_loader_rejects_paths_outside_configuration_directory(tmp_path: Path) -> None:
    store = LocalProviderConfigStore(tmp_path / "config" / "providers.json")
    provider = ProviderDefinition(
        provider_id="warehouse",
        display_name="Warehouse",
        transport=StdioMCPTransport(command="warehouse-mcp"),
    )
    configuration = LocalProviderConfiguration(
        providers=(provider,),
        bindings=(
            CapabilityBinding(
                canonical_capability=CanonicalCapability.GET_INVENTORY,
                provider_id="warehouse",
                tool_name="get_stock",
                mapping_file="../secret.json",
            ),
        ),
    )

    with pytest.raises(MCPAccessError, match="must stay inside"):
        load_mappings(store, configuration)
