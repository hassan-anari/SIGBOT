from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from sqlalchemy.exc import IntegrityError

from chainura.application.signalist_service import SignalistService
from chainura.domain.signalist import SignalistStatus
from chainura.errors import (
    DuplicateTelegramUserIdError,
    InvalidSignalistStatusTransitionError,
    SignalistPersistenceError,
)
from chainura.persistence.database import create_session_factory
from chainura.persistence.error_translation import translate_integrity_error
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from integration_tests.support import create_safe_engine, migrate, require_safe_test_database, truncate_signalists


class ServiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe = require_safe_test_database(); migrate("head", cls.safe)

    async def asyncSetUp(self) -> None:
        self.engine = create_safe_engine(self.safe); await truncate_signalists(self.engine, self.safe)
        self.sessions = create_session_factory(self.engine)
        self.service = SignalistService(SqlAlchemySignalistTransactionFactory(self.sessions))

    async def asyncTearDown(self) -> None: await self.engine.dispose()

    async def test_full_service_lifecycle_and_persistence(self) -> None:
        first = await self.service.create(100, "First")
        second = await self.service.create(200, "Second")
        inactive = await self.service.deactivate(first.id)  # type: ignore[arg-type]
        active = await self.service.activate(inactive.id)  # type: ignore[arg-type]
        archived = await self.service.archive(active.id)  # type: ignore[arg-type]
        second = await self.service.deactivate(second.id)  # type: ignore[arg-type]
        await self.service.archive(second.id)  # type: ignore[arg-type]
        self.assertIsNotNone(archived.archived_at)
        self.assertEqual(len(await self.service.list(SignalistStatus.ARCHIVED)), 2)
        self.assertEqual((await self.service.get_by_telegram_user_id(100)).id, first.id)

    async def test_duplicate_rolls_back_and_next_transaction_succeeds(self) -> None:
        await self.service.create(100, "First")
        with self.assertRaises(DuplicateTelegramUserIdError): await self.service.create(100, "Again")
        self.assertEqual((await self.service.create(200, "Second")).telegram_user_id, 200)

    async def test_integrity_translation_helper_paths(self) -> None:
        class Diagnostic: constraint_name = "uq_signalists_telegram_user_id"
        class DriverError(Exception): diag = Diagnostic()
        duplicate = IntegrityError("safe", {}, DriverError())
        self.assertIsInstance(translate_integrity_error(duplicate), DuplicateTelegramUserIdError)
        DriverError.diag.constraint_name = "ck_signalists_display_name_nonempty"
        other = IntegrityError("safe", {}, DriverError())
        self.assertIsInstance(translate_integrity_error(other), SignalistPersistenceError)

    async def test_concurrent_stale_activation_cannot_overwrite_archive(self) -> None:
        row = await self.service.create(300, "Concurrent")
        inactive = await self.service.deactivate(row.id)  # type: ignore[arg-type]

        transaction_factory = SqlAlchemySignalistTransactionFactory(self.sessions)
        archive_transaction = transaction_factory()
        await archive_transaction.__aenter__()
        try:
            locked = await archive_transaction.repository.get_by_id_for_transition(inactive.id)  # type: ignore[arg-type]
            self.assertIs(locked.status, SignalistStatus.INACTIVE)  # type: ignore[union-attr]
            approved = locked.approve_archive()  # type: ignore[union-attr]

            lock_attempted = asyncio.Event()

            class CoordinatedRepository:
                def __init__(self, wrapped: object) -> None:
                    self._wrapped = wrapped

                def __getattr__(self, name: str) -> object:
                    return getattr(self._wrapped, name)

                async def get_by_id_for_transition(self, signalist_id: int):
                    lock_attempted.set()
                    return await self._wrapped.get_by_id_for_transition(signalist_id)

            class CoordinatedTransaction:
                def __init__(self, wrapped: object) -> None: self._wrapped = wrapped
                async def __aenter__(self):
                    await self._wrapped.__aenter__()
                    self.repository = CoordinatedRepository(self._wrapped.repository)
                    return self
                async def commit(self): await self._wrapped.commit()
                async def __aexit__(self, exc_type, exc, traceback):
                    await self._wrapped.__aexit__(exc_type, exc, traceback)

            def coordinated_factory():
                return CoordinatedTransaction(transaction_factory())

            stale_activation = asyncio.create_task(
                SignalistService(coordinated_factory).activate(inactive.id)  # type: ignore[arg-type]
            )
            await lock_attempted.wait()
            archived = await archive_transaction.repository.persist_status(inactive.id, approved)  # type: ignore[arg-type]
            await archive_transaction.commit()
        finally:
            await archive_transaction.__aexit__(None, None, None)

        self.assertIs(archived.status, SignalistStatus.ARCHIVED)
        with self.assertRaises(InvalidSignalistStatusTransitionError):
            await stale_activation
        persisted = await self.service.get_by_id(inactive.id)  # type: ignore[arg-type]
        self.assertIs(persisted.status, SignalistStatus.ARCHIVED)
        self.assertIsNotNone(persisted.archived_at)
