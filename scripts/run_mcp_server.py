"""Run the composed authenticated Streamable HTTP MCP server."""

from __future__ import annotations

from civitas.runtime.main import main as runtime_main


def main() -> int:
    return runtime_main()


if __name__ == "__main__":
    raise SystemExit(main())
