from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine

from chainura.persistence.database import create_database_engine


class UnsafeIntegrationDatabaseError(RuntimeError):
    pass


@dataclass(slots=True)
class SafeTestDatabase:
    temporary_directory: tempfile.TemporaryDirectory[str]
    path: Path
    url: URL

    @property
    def rendered(self) -> str:
        return self.url.render_as_string(hide_password=False)

    def cleanup(self) -> None:
        self.temporary_directory.cleanup()


def require_safe_test_database(
    mapping: dict[str, str] | os._Environ[str] | None = None,
) -> SafeTestDatabase:
    """Create an isolated file database; external database URLs are never accepted."""
    temporary_directory = tempfile.TemporaryDirectory(prefix="chainura-sqlite-test-")
    root = Path(temporary_directory.name).resolve()
    path = root / "chainura-test.db"
    if path.parent != root or root == Path.cwd().resolve():
        temporary_directory.cleanup()
        raise UnsafeIntegrationDatabaseError("temporary SQLite isolation could not be established")
    url = URL.create("sqlite+aiosqlite", database=str(path))
    return SafeTestDatabase(temporary_directory, path, url)


def migrate(target: str, safe: SafeTestDatabase) -> None:
    if not isinstance(safe, SafeTestDatabase) or safe.path.parent != Path(safe.temporary_directory.name):
        raise UnsafeIntegrationDatabaseError("validated temporary test database is required")
    environment = dict(os.environ, DATABASE_URL=safe.rendered, PYTHON_DOTENV_DISABLED="1")
    action = "downgrade" if target == "base" else "upgrade"
    subprocess.run(
        ["alembic", action, target],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        check=True,
    )


def create_safe_engine(safe: SafeTestDatabase) -> AsyncEngine:
    if not isinstance(safe, SafeTestDatabase):
        raise UnsafeIntegrationDatabaseError("validated temporary test database is required")
    return create_database_engine(safe.rendered)


async def truncate_signalists(engine: AsyncEngine, safe: SafeTestDatabase) -> None:
    if not isinstance(safe, SafeTestDatabase):
        raise UnsafeIntegrationDatabaseError("validated temporary test database is required")
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM signalists"))
