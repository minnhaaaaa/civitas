"""Deployment handoff point for the deterministic simulated MCP provider."""

from __future__ import annotations

import os
import shlex
import sys


def main() -> int:
    command = os.getenv("CIVITAS_SIMULATED_PROVIDER_COMMAND")
    if not command:
        print("CIVITAS_SIMULATED_PROVIDER_COMMAND is required for the provider.", file=sys.stderr)
        return 78
    parts = shlex.split(command)
    if not parts:
        return 78
    os.execvp(parts[0], parts)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
