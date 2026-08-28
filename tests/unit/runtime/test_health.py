from datetime import UTC, datetime

import pytest

from civitas.runtime.config import RuntimeSettings
from civitas.runtime.health import EXPECTED_DATABASE_REVISION, RuntimeHealth


class FakeHealthStore:
    def __init__(self, *, alive: bool = True, revision: str | None = None, worker: bool = True):
        self.alive = alive
        self.revision = revision or EXPECTED_DATABASE_REVISION
        self.worker = worker
        self.heartbeats: list[tuple[str, str, str]] = []

    async def database_alive(self) -> bool:
        return self.alive

    async def database_revision(self) -> str | None:
        return self.revision

    async def is_ready(self, **kwargs: object) -> bool:
        del kwargs
        return self.worker

    async def heartbeat(
        self, *, service_id: str, service_kind: str, now: datetime, state: str = "running"
    ) -> None:
        del now
        self.heartbeats.append((service_id, service_kind, state))


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 28, tzinfo=UTC)


def _settings(**changes: object) -> RuntimeSettings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://civitas:secret@database/civitas",
        "approval_secret_pepper": "a" * 32,
        "bearer_token": "b" * 32,
        "organization_id": "org-1",
        "operator_id": "operator-1",
        "operator_subject": "subject-1",
        "operator_roles": ("procurement-operator",),
    }
    values.update(changes)
    return RuntimeSettings(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_readiness_requires_exact_migration_and_recent_worker() -> None:
    store = FakeHealthStore(revision="old", worker=False)
    health = RuntimeHealth(
        store=store,  # type: ignore[arg-type]
        clock=FixedClock(),
        settings=_settings(require_worker_ready=True),
        service_id="mcp-1",
    )

    ready, reasons = await health.ready()

    assert ready is False
    assert reasons == ("migration_revision_mismatch", "worker_unavailable")


@pytest.mark.asyncio
async def test_database_failure_short_circuits_readiness() -> None:
    health = RuntimeHealth(
        store=FakeHealthStore(alive=False),  # type: ignore[arg-type]
        clock=FixedClock(),
        settings=_settings(),
        service_id="mcp-1",
    )

    assert await health.ready() == (False, ("database_unavailable",))


@pytest.mark.asyncio
async def test_shutdown_persists_stopping_heartbeat() -> None:
    store = FakeHealthStore()
    health = RuntimeHealth(
        store=store,  # type: ignore[arg-type]
        clock=FixedClock(),
        settings=_settings(),
        service_id="mcp-1",
    )

    await health.start()
    await health.stop()

    assert store.heartbeats[0] == ("mcp-1", "mcp-server", "starting")
    assert store.heartbeats[-1] == ("mcp-1", "mcp-server", "stopping")
