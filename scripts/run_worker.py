"""Compatibility wrapper for the durable worker CLI."""

from __future__ import annotations

from civitas.worker.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
