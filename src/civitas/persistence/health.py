"""PostgreSQL operational heartbeat and readiness projections."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civitas.persistence.models import ServiceHeartbeatModel


class PostgreSQLServiceHealthStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def heartbeat(
        self,
        *,
        service_id: str,
        service_kind: str,
        now: datetime,
        state: str = "running",
    ) -> None:
        if now.tzinfo is None:
            raise ValueError("heartbeat time must be timezone-aware")
        statement = insert(ServiceHeartbeatModel).values(
            service_id=service_id,
            service_kind=service_kind,
            state=state,
            started_at=now,
            last_seen_at=now,
            metadata_json={},
        )
        statement = statement.on_conflict_do_update(
            index_elements=[ServiceHeartbeatModel.service_id],
            set_={
                "service_kind": service_kind,
                "state": state,
                "last_seen_at": now,
            },
        )
        async with self._sessions() as session, session.begin():
            await session.execute(statement)

    async def is_ready(
        self,
        *,
        service_kind: str,
        now: datetime,
        max_age: timedelta,
    ) -> bool:
        cutoff = now - max_age
        async with self._sessions() as session:
            row = await session.scalar(
                select(ServiceHeartbeatModel.service_id)
                .where(
                    ServiceHeartbeatModel.service_kind == service_kind,
                    ServiceHeartbeatModel.state == "running",
                    ServiceHeartbeatModel.last_seen_at >= cutoff,
                )
                .limit(1)
            )
        return row is not None

    async def database_revision(self) -> str | None:
        async with self._sessions() as session:
            revision: object = await session.scalar(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
        return revision if isinstance(revision, str) else None

    async def database_alive(self) -> bool:
        try:
            async with self._sessions() as session:
                value: object = await session.scalar(text("SELECT 1"))
            return value == 1
        except Exception:
            return False
