from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context

from chainura.config import validate_database_url
from chainura.environment import load_project_environment
from chainura.persistence.base import Base
from chainura.persistence.database import create_database_engine
from chainura.persistence import models  # noqa: F401 - registers metadata
from chainura.persistence.sqlite_migration import (run_with_sqlite_foreign_keys_suspended,
    validate_sqlite_foreign_keys)

load_project_environment()
config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required to run migrations")
    return validate_database_url(value)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(), target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"}, compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    # SQLite batch migrations rebuild referenced tables. Foreign-key enforcement
    # during the temporary DROP can cascade-delete valid child workflow rows.
    # Migrations run with enforcement suspended, then restore and verify it before
    # returning the connection to the application.
    def migrate() -> None:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
            # Validate before Alembic can finalize its migration transaction.
            validate_sqlite_foreign_keys(connection)
    run_with_sqlite_foreign_keys_suspended(connection,migrate)


async def run_async_migrations() -> None:
    engine = create_database_engine(database_url())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
