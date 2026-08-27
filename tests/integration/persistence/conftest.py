import os
from collections.abc import AsyncIterator

import pytest_asyncio
from alembic import command
from alembic.config import Config

from civitas.persistence.database import Database


def database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://civitas:civitas_dev@localhost:55432/civitas",
    )


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def database() -> AsyncIterator[Database]:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url())
    command.upgrade(config, "head")
    database = Database(database_url())
    try:
        yield database
    finally:
        await database.dispose()
