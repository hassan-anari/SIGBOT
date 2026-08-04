from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from chainura.persistence.error_translation import persistence_error
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.destination_repository import SqlAlchemyDestinationRepository
from chainura.persistence.signal_repository import SqlAlchemySignalRepository
from chainura.persistence.template_repository import SqlAlchemyTemplateRepository
from chainura.persistence.error_translation import commit_persistence_error
from chainura.errors import TemplatePersistenceError


class SqlAlchemySignalistTransaction:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._session: AsyncSession | None = None
        self._committed = False
        self._error_family = "signalist"
        self.repository: SqlAlchemySignalistRepository
        self.signalists: SqlAlchemySignalistRepository
        self.destinations: SqlAlchemyDestinationRepository
        self.signals: SqlAlchemySignalRepository
        self.templates: SqlAlchemyTemplateRepository

    async def __aenter__(self) -> Self:
        self._session = self._sessions()
        try:
            await self._session.begin()
        except SQLAlchemyError as exc:
            await self._session.close()
            raise persistence_error(exc) from exc
        self.repository = SqlAlchemySignalistRepository(self._session)
        self.signalists = self.repository
        self.destinations = SqlAlchemyDestinationRepository(self._session)
        self.signals = SqlAlchemySignalRepository(self._session)
        self.templates = SqlAlchemyTemplateRepository(self._session)
        return self

    def use_destination_errors(self) -> None:
        self._error_family = "destination"

    def use_template_errors(self) -> None:
        self._error_family = "template"

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("transaction has not started")
        try:
            await self._session.commit()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            if self._error_family == "template":
                raise TemplatePersistenceError("template metadata commit failed") from exc
            raise commit_persistence_error(exc, self._error_family) from exc
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
