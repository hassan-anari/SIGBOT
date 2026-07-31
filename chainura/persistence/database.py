from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from chainura.config import validate_database_url


def create_database_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(validate_database_url(database_url))


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def dispose_database_engine(engine: AsyncEngine) -> None:
    await engine.dispose()
