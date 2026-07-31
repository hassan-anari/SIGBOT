from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chainura.persistence.error_translation import persistence_error
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository


class SqlAlchemySignalistTransaction:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._session: AsyncSession | None = None
        self._committed = False
        self.repository: SqlAlchemySignalistRepository

    async def __aenter__(self) -> Self:
        self._session = self._sessions()
        try:
            await self._session.begin()
        except SQLAlchemyError as exc:
            await self._session.close()
            raise persistence_error(exc) from exc
        self.repository = SqlAlchemySignalistRepository(self._session)
        return self

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("transaction has not started")
        try:
            await self._session.commit()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise persistence_error(exc) from exc
        self._committed = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._session is None:
            return
        try:
            if not self._committed and self._session.in_transaction():
                await self._session.rollback()
        finally:
            await self._session.close()


class SqlAlchemySignalistTransactionFactory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    def __call__(self) -> SqlAlchemySignalistTransaction:
        return SqlAlchemySignalistTransaction(self._sessions)
