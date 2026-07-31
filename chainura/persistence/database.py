from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from chainura.config import validate_database_url
from chainura.errors import DatabaseInitializationError


def _configure_sqlite_connection(dbapi_connection: object, _record: object) -> None:
    cursor = None
    try:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.execute("PRAGMA journal_mode = WAL")
        journal_mode = cursor.fetchone()[0]
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA foreign_keys")
        foreign_keys = cursor.fetchone()[0]
        cursor.execute("PRAGMA busy_timeout")
        busy_timeout = cursor.fetchone()[0]
        if str(journal_mode).lower() != "wal" or foreign_keys != 1 or busy_timeout != 5000:
            raise DatabaseInitializationError(
                "SQLite did not accept the required runtime configuration"
            )
    except DatabaseInitializationError:
        raise
    except Exception as exc:
        raise DatabaseInitializationError("SQLite initialization failed") from exc
    finally:
        if cursor is not None:
            cursor.close()


def ensure_database_parent(database_url: str) -> None:
    """Create the SQLite parent directory during runtime composition only."""
    url = make_url(validate_database_url(database_url))
    path = Path(url.database or "")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DatabaseInitializationError(
            "SQLite database parent directory could not be created"
        ) from exc


def create_database_engine(database_url: str) -> AsyncEngine:
    validated = validate_database_url(database_url)
    ensure_database_parent(validated)
    engine = create_async_engine(validated)
    event.listen(engine.sync_engine, "connect", _configure_sqlite_connection)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def initialize_database_engine(engine: AsyncEngine) -> None:
    """Open one startup connection so required SQLite PRAGMAs are verified."""
    try:
        async with engine.connect():
            pass
    except DatabaseInitializationError:
        raise
    except Exception as exc:
        raise DatabaseInitializationError("SQLite startup verification failed") from exc


async def dispose_database_engine(engine: AsyncEngine) -> None:
    await engine.dispose()
