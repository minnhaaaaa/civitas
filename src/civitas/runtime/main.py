"""CLI for the composed Civitas MCP runtime."""

from __future__ import annotations

import sys

import uvicorn

from civitas.runtime.composition import build_runtime
from civitas.runtime.config import RuntimeSettings, SettingsError


def main() -> int:
    try:
        settings = RuntimeSettings.from_env()
    except SettingsError as error:
        print(f"Civitas configuration error: {error}", file=sys.stderr)
        return 78

    runtime = build_runtime(settings)
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
