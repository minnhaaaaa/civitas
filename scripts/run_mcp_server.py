"""Run the configured authenticated Streamable HTTP MCP server command."""

from __future__ import annotations

import os
import shlex
import sys


def main() -> int:
    command = os.getenv("CIVITAS_MCP_SERVER_COMMAND")
    if not command:
        print("CIVITAS_MCP_SERVER_COMMAND is required for the MCP server.", file=sys.stderr)
        return 78
    parts = shlex.split(command)
    if not parts:
        return 78
    os.execvp(parts[0], parts)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
