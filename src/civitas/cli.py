"""Local Civitas administration CLI."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from civitas.contracts.provider_config import (
    CanonicalCapability,
    CapabilityBinding,
    HttpMCPTransport,
    LocalProviderConfiguration,
    ProviderDefinition,
    ProviderMode,
    ProviderTransport,
    StdioMCPTransport,
)
from civitas.contracts.providers import ProviderAccessContext
from civitas.integrations.provider_config import LocalProviderConfigStore


class CLIError(ValueError):
    """Safe user-facing configuration error."""


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    store = LocalProviderConfigStore(arguments.config)
    try:
        return _dispatch(store, arguments)
    except (CLIError, ValueError) as error:
        parser.error(str(error))
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="civitas")
    parser.add_argument("--config", type=Path, default=_default_config_path())
    commands = parser.add_subparsers(dest="command", required=True)
    providers = commands.add_parser("providers", help="configure operational MCP providers")
    provider_commands = providers.add_subparsers(dest="provider_command", required=True)

    add = provider_commands.add_parser("add", help="register a local MCP provider")
    add.add_argument("provider_id")
    add.add_argument("--name", required=True)
    add.add_argument("--transport", choices=("stdio", "http"), required=True)
    add.add_argument("--command", dest="stdio_command")
    add.add_argument("--arg", action="append", default=[])
    add.add_argument("--url")
    for context in ProviderAccessContext:
        add.add_argument(f"--{context.value}-env", action="append", default=[])
        add.add_argument(f"--{context.value}-auth-env")

    mapping = provider_commands.add_parser("map", help="bind a provider tool")
    mapping.add_argument("provider_tool")
    mapping.add_argument("canonical_capability", choices=tuple(CanonicalCapability))
    mapping.add_argument("--mapping")

    enable = provider_commands.add_parser("enable", help="enable a validated provider")
    enable.add_argument("provider_id")
    disable = provider_commands.add_parser("disable", help="disable a provider")
    disable.add_argument("provider_id")
    provider_commands.add_parser("list", help="list configured providers")
    provider_commands.add_parser("use-sandbox", help="use the simulated provider")
    provider_commands.add_parser("use-live", help="use configured live providers")
    return parser


def _dispatch(store: LocalProviderConfigStore, arguments: argparse.Namespace) -> int:
    if arguments.command != "providers":
        raise CLIError("unknown command")
    configuration = store.load()
    command = arguments.provider_command
    if command == "add":
        updated = _add_provider(configuration, arguments)
    elif command == "map":
        updated = _map_capability(configuration, arguments)
    elif command in {"enable", "disable"}:
        updated = _set_provider_enabled(
            configuration,
            arguments.provider_id,
            enabled=command == "enable",
        )
    elif command == "use-sandbox":
        updated = _with_mode(configuration, ProviderMode.SANDBOX)
    elif command == "use-live":
        updated = _with_mode(configuration, ProviderMode.LIVE)
    elif command == "list":
        _print_configuration(configuration)
        return 0
    else:  # pragma: no cover - argparse constrains the command
        raise CLIError("unknown provider command")
    store.save(updated)
    return 0


def _add_provider(
    configuration: LocalProviderConfiguration, arguments: argparse.Namespace
) -> LocalProviderConfiguration:
    if any(item.provider_id == arguments.provider_id for item in configuration.providers):
        raise CLIError(f"provider already exists: {arguments.provider_id}")
    transport: ProviderTransport
    if arguments.transport == "stdio":
        if not arguments.stdio_command or arguments.url:
            raise CLIError("STDIO providers require --command and cannot use --url")
        credential_refs = {
            context: _environment_pairs(getattr(arguments, f"{context.value}_env"))
            for context in ProviderAccessContext
            if getattr(arguments, f"{context.value}_env")
        }
        transport = StdioMCPTransport(
            command=arguments.stdio_command,
            args=tuple(arguments.arg),
            credential_env_refs=credential_refs,
        )
    else:
        if not arguments.url or arguments.stdio_command or arguments.arg:
            raise CLIError("HTTP providers require --url and cannot use STDIO command fields")
        authorization_refs = {
            context: reference
            for context in ProviderAccessContext
            if (reference := getattr(arguments, f"{context.value}_auth_env"))
        }
        transport = HttpMCPTransport(
            url=arguments.url,
            authorization_env_refs=authorization_refs,
        )
    provider = ProviderDefinition(
        provider_id=arguments.provider_id,
        display_name=arguments.name,
        transport=transport,
    )
    return LocalProviderConfiguration(
        mode=configuration.mode,
        providers=(*configuration.providers, provider),
        bindings=configuration.bindings,
    )


def _map_capability(
    configuration: LocalProviderConfiguration, arguments: argparse.Namespace
) -> LocalProviderConfiguration:
    provider_id, separator, tool_name = arguments.provider_tool.partition(".")
    if not separator or not tool_name:
        raise CLIError("provider tool must use provider_id.tool_name syntax")
    binding = CapabilityBinding(
        canonical_capability=CanonicalCapability(arguments.canonical_capability),
        provider_id=provider_id,
        tool_name=tool_name,
        mapping_file=arguments.mapping,
    )
    retained = tuple(
        item
        for item in configuration.bindings
        if item.canonical_capability is not binding.canonical_capability
    )
    return LocalProviderConfiguration(
        mode=configuration.mode,
        providers=configuration.providers,
        bindings=(*retained, binding),
    )


def _set_provider_enabled(
    configuration: LocalProviderConfiguration,
    provider_id: str,
    *,
    enabled: bool,
) -> LocalProviderConfiguration:
    if not any(item.provider_id == provider_id for item in configuration.providers):
        raise CLIError(f"unknown provider: {provider_id}")
    providers = tuple(
        item.model_copy(update={"enabled": enabled}) if item.provider_id == provider_id else item
        for item in configuration.providers
    )
    return LocalProviderConfiguration(
        mode=configuration.mode,
        providers=providers,
        bindings=configuration.bindings,
    )


def _with_mode(
    configuration: LocalProviderConfiguration, mode: ProviderMode
) -> LocalProviderConfiguration:
    return LocalProviderConfiguration(
        mode=mode,
        providers=configuration.providers,
        bindings=configuration.bindings,
    )


def _environment_pairs(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        child_name, separator, local_reference = value.partition("=")
        if not separator or not child_name or not local_reference:
            raise CLIError("credential environment mappings must use CHILD_NAME=LOCAL_REFERENCE")
        result[child_name] = local_reference
    return result


def _print_configuration(configuration: LocalProviderConfiguration) -> None:
    print(f"mode: {configuration.mode.value}")
    if not configuration.providers:
        print("providers: none")
        return
    for provider in configuration.providers:
        state = "enabled" if provider.enabled else "disabled"
        print(f"{provider.provider_id}\t{provider.transport.kind}\t{state}")


def _default_config_path() -> Path:
    provider_config = os.environ.get("CIVITAS_PROVIDER_CONFIG")
    if provider_config:
        return Path(provider_config)
    explicit = os.environ.get("CIVITAS_CONFIG_HOME")
    if explicit:
        return Path(explicit) / "providers.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "civitas" / "providers.json"
    return Path.home() / ".config" / "civitas" / "providers.json"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
