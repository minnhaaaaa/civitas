"""Deployment handoff point for the durable worker implementation.

The worker is deliberately not emulated here: pretending that a no-op process
is a planner would make readiness and recovery claims unsafe.  Agent 4 supplies
``civitas.worker.main``; the local MCP profile fails closed until that package is
present in the assembled release.
"""

from __future__ import annotations

import os
import shlex
import sys


def main() -> int:
    command = os.getenv("CIVITAS_WORKER_COMMAND")
    if not command:
        print("CIVITAS_WORKER_COMMAND is required for the durable worker.", file=sys.stderr)
        return 78
    parts = shlex.split(command)
    if not parts:
        return 78
    os.execvp(parts[0], parts)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
