"""Time source interface for deterministic workflows and tests."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current timezone-aware instant."""
        ...
