from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from civitas.persistence.health import PostgreSQLServiceHealthStore
from civitas.persistence.models import ServiceHeartbeatModel
from civitas.runtime.health import EXPECTED_DATABASE_REVISION


@pytest.mark.asyncio
async def test_worker_heartbeat_is_durable_and_expires(database: object) -> None:
    service_id = f"worker-{uuid4().hex}"
    now = datetime.now(UTC)
    store = PostgreSQLServiceHealthStore(database.sessions)  # type: ignore[attr-defined]
    try:
        await store.heartbeat(
            service_id=service_id,
            service_kind="worker",
            now=now,
        )
        assert await store.is_ready(service_kind="worker", now=now, max_age=timedelta(seconds=30))
        assert not await store.is_ready(
            service_kind="worker", now=now + timedelta(minutes=2), max_age=timedelta(seconds=30)
        )
        await store.heartbeat(
            service_id=service_id,
            service_kind="worker",
            now=now + timedelta(minutes=2),
            state="stopping",
        )
        assert not await store.is_ready(
            service_kind="worker",
            now=now + timedelta(minutes=2),
            max_age=timedelta(seconds=30),
        )
    finally:
        async with database.sessions() as session, session.begin():  # type: ignore[attr-defined]
            await session.execute(
                delete(ServiceHeartbeatModel).where(ServiceHeartbeatModel.service_id == service_id)
            )


@pytest.mark.asyncio
async def test_health_store_reads_applied_migration_revision(database: object) -> None:
    store = PostgreSQLServiceHealthStore(database.sessions)  # type: ignore[attr-defined]
    assert await store.database_alive()
    assert await store.database_revision() == EXPECTED_DATABASE_REVISION
