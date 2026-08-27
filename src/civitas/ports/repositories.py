"""Minimal repository and unit-of-work protocols."""

from collections.abc import Sequence
from typing import Protocol, TypeVar

T = TypeVar("T")


class Repository(Protocol[T]):
    async def get(self, entity_id: str) -> T | None: ...

    async def add(self, entity: T) -> None: ...

    async def list(self, *, limit: int = 100, offset: int = 0) -> Sequence[T]: ...


class UnitOfWork(Protocol):
    async def __aenter__(self) -> "UnitOfWork": ...

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
