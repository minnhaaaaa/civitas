"""User-facing CLI for explicit local MCP provider configuration."""

from pathlib import Path

import pytest

from civitas.cli import main
from civitas.contracts.provider_config import CanonicalCapability, ProviderMode
from civitas.integrations.provider_config import LocalProviderConfigStore


def _run(config_path: Path, *arguments: str) -> int:
    return main(["--config", str(config_path), *arguments])


def test_cli_adds_enables_and_maps_a_stdio_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "providers.json"

    assert (
        _run(
            config_path,
            "providers",
            "add",
            "warehouse",
            "--name",
            "Warehouse MCP",
            "--transport",
            "stdio",
            "--command",
            "uvx",
            "--arg",
            "warehouse-mcp",
            "--planning-env",
            "WAREHOUSE_TOKEN=WAREHOUSE_PLANNING_TOKEN",
            "--dissent-env",
            "WAREHOUSE_TOKEN=WAREHOUSE_DISSENT_TOKEN",
        )
        == 0
    )
    assert (
        _run(
            config_path,
            "providers",
            "map",
            "warehouse.get_stock",
            CanonicalCapability.GET_INVENTORY.value,
        )
        == 0
    )
    assert _run(config_path, "providers", "enable", "warehouse") == 0

    configuration = LocalProviderConfigStore(config_path).load()
    assert configuration.providers[0].enabled is True
    assert configuration.providers[0].transport.command == "uvx"
    assert configuration.bindings[0].tool_name == "get_stock"


def test_cli_adds_http_provider_with_credential_references(tmp_path: Path) -> None:
    config_path = tmp_path / "providers.json"

    result = _run(
        config_path,
        "providers",
        "add",
        "purchasing",
        "--name",
        "Purchasing MCP",
        "--transport",
        "http",
        "--url",
        "https://purchasing.example.com/mcp",
        "--planning-auth-env",
        "PURCHASING_READ_TOKEN",
        "--dissent-auth-env",
        "PURCHASING_DISSENT_TOKEN",
        "--execution-auth-env",
        "PURCHASING_WRITE_TOKEN",
    )

    assert result == 0
    configuration = LocalProviderConfigStore(config_path).load()
    transport = configuration.providers[0].transport
    assert transport.kind == "streamable_http"
    assert transport.authorization_env_refs["execution"] == "PURCHASING_WRITE_TOKEN"


def test_cli_switches_modes_without_deleting_live_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "providers.json"
    assert (
        _run(
            config_path,
            "providers",
            "add",
            "warehouse",
            "--name",
            "Warehouse MCP",
            "--transport",
            "stdio",
            "--command",
            "warehouse-mcp",
        )
        == 0
    )

    assert _run(config_path, "providers", "use-sandbox") == 0
    sandbox = LocalProviderConfigStore(config_path).load()
    assert sandbox.mode is ProviderMode.SANDBOX
    assert len(sandbox.providers) == 1

    assert _run(config_path, "providers", "use-live") == 0
    assert LocalProviderConfigStore(config_path).load().mode is ProviderMode.LIVE


def test_cli_list_does_not_resolve_or_print_secret_values(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "providers.json"
    monkeypatch.setenv("PURCHASING_WRITE_TOKEN", "must-not-print")
    assert (
        _run(
            config_path,
            "providers",
            "add",
            "purchasing",
            "--name",
            "Purchasing MCP",
            "--transport",
            "http",
            "--url",
            "https://purchasing.example.com/mcp",
            "--execution-auth-env",
            "PURCHASING_WRITE_TOKEN",
        )
        == 0
    )

    assert _run(config_path, "providers", "list") == 0

    output = capsys.readouterr().out
    assert "purchasing" in output
    assert "must-not-print" not in output
