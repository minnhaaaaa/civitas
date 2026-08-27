"""Identifier generation interface for deterministic workflows and tests."""

from typing import Protocol


class IDGenerator(Protocol):
    def new_id(self, namespace: str) -> str:
        """Return a new stable identifier in the requested namespace."""
        ...
