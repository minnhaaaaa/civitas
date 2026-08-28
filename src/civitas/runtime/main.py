"""CLI for the composed Civitas MCP runtime."""

from __future__ import annotations

import asyncio
import sys

import uvicorn

from civitas.runtime.bootstrap import load_provider_runtime
from civitas.runtime.composition import build_runtime
from civitas.runtime.config import RuntimeSettings, SettingsError
from civitas.runtime.observability import configure_logging


def main() -> int:
    try:
        settings = RuntimeSettings.from_env()
    except SettingsError as error:
        print(f"Civitas configuration error: {error}", file=sys.stderr)
        return 78

    configure_logging(
        service=settings.service_name,
        environment=settings.environment,
        level=settings.log_level,
        log_format=settings.log_format,
    )
    try:
        provider = asyncio.run(load_provider_runtime(settings))
    except SettingsError as error:
        print(f"Civitas provider configuration error: {error}", file=sys.stderr)
        return 78
    runtime = build_runtime(
        settings,
        provider_planning=None if provider is None else provider.planning,
        provider_execution=None if provider is None else provider.execution,
    )
    if settings.transport == "stdio":
        runtime.mcp_server.run_stdio()
        return 0
    uvicorn.run(
        runtime.http_app(),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        proxy_headers=True,
    )
    return 0
