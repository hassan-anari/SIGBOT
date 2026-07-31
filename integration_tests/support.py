from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from chainura.persistence.database import create_database_engine


class UnsafeIntegrationDatabaseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SafeTestDatabase:
    url: URL

    @property
    def rendered(self) -> str:
        return self.url.render_as_string(hide_password=False)


def require_safe_test_database(mapping: dict[str, str] | os._Environ[str] | None = None) -> SafeTestDatabase:
    environment = os.environ if mapping is None else mapping
    raw = environment.get("TEST_DATABASE_URL")
    if not raw or not raw.strip():
        raise UnsafeIntegrationDatabaseError("TEST_DATABASE_URL is required for destructive integration tests")
    try:
        url = make_url(raw.strip())
    except Exception as exc:
        raise UnsafeIntegrationDatabaseError("TEST_DATABASE_URL is invalid") from exc
    if url.drivername != "postgresql+psycopg":
        raise UnsafeIntegrationDatabaseError("TEST_DATABASE_URL must use postgresql+psycopg")
    if not url.database or not url.database.endswith("_test"):
        raise UnsafeIntegrationDatabaseError("TEST_DATABASE_URL database name must end with _test")
    application_raw = environment.get("DATABASE_URL")
    if application_raw:
        try:
            application_url = make_url(application_raw)
        except Exception as exc:
            raise UnsafeIntegrationDatabaseError("DATABASE_URL is invalid") from exc
        if application_url == url and not url.database.endswith("_test"):
            raise UnsafeIntegrationDatabaseError("equal database URLs are safe only for an _test database")
    return SafeTestDatabase(url)


def migrate(target: str, safe: SafeTestDatabase) -> None:
    if not isinstance(safe, SafeTestDatabase):
        raise UnsafeIntegrationDatabaseError("validated test database is required")
    environment = dict(os.environ, DATABASE_URL=safe.rendered)
    action = "downgrade" if target == "base" else "upgrade"
    subprocess.run(
        ["alembic", action, target],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        check=True,
    )


def create_safe_engine(safe: SafeTestDatabase) -> AsyncEngine:
    if not isinstance(safe, SafeTestDatabase):
        raise UnsafeIntegrationDatabaseError("validated test database is required")
    return create_database_engine(safe.rendered)


async def truncate_signalists(engine: AsyncEngine, safe: SafeTestDatabase) -> None:
    if not isinstance(safe, SafeTestDatabase):
        raise UnsafeIntegrationDatabaseError("validated test database is required")
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE signalists RESTART IDENTITY"))
