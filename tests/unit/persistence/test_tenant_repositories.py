"""Tenant repository predicates are mandatory even for globally unique IDs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from civitas.domain.planning import PlanningRun
from civitas.persistence.tenant_repositories import TenantRepositories


class EmptyScalarSession:
    def __init__(self) -> None:
        self.statements: list[object] = []
        self.added: list[object] = []

    async def scalar(self, statement: object) -> None:
        self.statements.append(statement)
        return None

    def add(self, entity: object) -> None:
        self.added.append(entity)


@pytest.mark.asyncio
async def test_direct_lookup_includes_organization_predicate() -> None:
    session = EmptyScalarSession()
    repositories = TenantRepositories(session, "org-a")  # type: ignore[arg-type]

    assert await repositories.planning_runs.get("run-owned-by-org-b") is None

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "planning_runs.id = 'run-owned-by-org-b'" in sql
    assert "planning_runs.organization_id = 'org-a'" in sql


@pytest.mark.asyncio
async def test_cross_tenant_insert_is_rejected_before_session_add() -> None:
    session = EmptyScalarSession()
    repositories = TenantRepositories(session, "org-a")  # type: ignore[arg-type]
    foreign_run = PlanningRun(
        id="run-b",
        organization_id="org-b",
        horizon_start=datetime(2026, 8, 28, tzinfo=UTC),
        horizon_end=datetime(2026, 9, 4, tzinfo=UTC),
        bucket_duration=timedelta(days=1),
        timezone="UTC",
        input_data_version="inputs-v1",
    )

    with pytest.raises(PermissionError, match="another organization"):
        await repositories.planning_runs.add(foreign_run)

    assert session.added == []
