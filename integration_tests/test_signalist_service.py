from __future__ import annotations

import asyncio
import unittest

from chainura.application.signalist_service import SignalistService
from chainura.domain.signalist import SignalistStatus
from chainura.errors import DuplicateTelegramUserIdError, InvalidSignalistStatusTransitionError
from chainura.persistence.database import create_session_factory
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from integration_tests.support import create_safe_engine, migrate, require_safe_test_database, truncate_signalists


class ServiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe = require_safe_test_database(); migrate("head", cls.safe)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.safe.cleanup()

    async def asyncSetUp(self) -> None:
        self.engine = create_safe_engine(self.safe); await truncate_signalists(self.engine, self.safe)
        self.sessions = create_session_factory(self.engine)
        self.factory = SqlAlchemySignalistTransactionFactory(self.sessions)
        self.service = SignalistService(self.factory)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_full_lifecycle_filters_archive_terminal_and_duplicate(self) -> None:
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
        with self.assertRaises(InvalidSignalistStatusTransitionError):
            await self.service.activate(archived.id)  # type: ignore[arg-type]
        with self.assertRaises(DuplicateTelegramUserIdError):
            await self.service.create(100, "Again")

    async def _assert_stale_transition_cannot_overwrite_archive(
        self, initial: SignalistStatus, stale_target: SignalistStatus
    ) -> None:
        row = await self.service.create(300, "Concurrent")
        if initial is SignalistStatus.INACTIVE:
            row = await self.service.deactivate(row.id)  # type: ignore[arg-type]

        read_complete = asyncio.Event()
        allow_cas = asyncio.Event()
        stale_factory = self.factory

        class CoordinatedRepository:
            def __init__(self, wrapped): self._wrapped = wrapped
            def __getattr__(self, name): return getattr(self._wrapped, name)
            async def get_by_id(self, signalist_id):
                result = await self._wrapped.get_by_id(signalist_id)
                if not read_complete.is_set():
                    read_complete.set(); await allow_cas.wait()
                return result

        class CoordinatedTransaction:
            def __init__(self, wrapped): self._wrapped = wrapped
            async def __aenter__(self):
                await self._wrapped.__aenter__()
                self.repository = CoordinatedRepository(self._wrapped.repository)
                return self
            async def commit(self): await self._wrapped.commit()
            async def __aexit__(self, *args): await self._wrapped.__aexit__(*args)

        stale_service = SignalistService(lambda: CoordinatedTransaction(stale_factory()))
        operation = stale_service.activate if stale_target is SignalistStatus.ACTIVE else stale_service.deactivate
        stale = asyncio.create_task(operation(row.id))  # type: ignore[arg-type]
        await read_complete.wait()
        archived = await self.service.archive(row.id)  # type: ignore[arg-type]
        allow_cas.set()
        with self.assertRaises(InvalidSignalistStatusTransitionError):
            await stale
        persisted = await self.service.get_by_id(row.id)  # type: ignore[arg-type]
        self.assertIs(archived.status, SignalistStatus.ARCHIVED)
        self.assertIs(persisted.status, SignalistStatus.ARCHIVED)

    async def test_stale_activation_cannot_resurrect_archive(self) -> None:
        await self._assert_stale_transition_cannot_overwrite_archive(
            SignalistStatus.INACTIVE, SignalistStatus.ACTIVE
        )

    async def test_stale_deactivation_cannot_overwrite_archive(self) -> None:
        await self._assert_stale_transition_cannot_overwrite_archive(
            SignalistStatus.ACTIVE, SignalistStatus.INACTIVE
        )

    async def _assert_archive_retries_after_concurrent_transition(
        self, initial: SignalistStatus, winner: SignalistStatus
    ) -> None:
        row = await self.service.create(400, "Archive retry")
        if initial is SignalistStatus.INACTIVE:
            row = await self.service.deactivate(row.id)  # type: ignore[arg-type]

        initial_read = asyncio.Event()
        release_archive = asyncio.Event()
        commits = 0

        class CoordinatedRepository:
            def __init__(self, wrapped): self._wrapped = wrapped
            def __getattr__(self, name): return getattr(self._wrapped, name)
            async def get_by_id(self, signalist_id):
                result = await self._wrapped.get_by_id(signalist_id)
                initial_read.set()
                await release_archive.wait()
                return result

        class CoordinatedTransaction:
            def __init__(self, wrapped): self._wrapped = wrapped
            async def __aenter__(self):
                await self._wrapped.__aenter__()
                self.repository = CoordinatedRepository(self._wrapped.repository)
                return self
            async def commit(self):
                nonlocal commits
                commits += 1
                await self._wrapped.commit()
            async def __aexit__(self, *args): await self._wrapped.__aexit__(*args)

        archive_service = SignalistService(
            lambda: CoordinatedTransaction(self.factory())
        )
        archive_task = asyncio.create_task(archive_service.archive(row.id))  # type: ignore[arg-type]
        await initial_read.wait()
        if winner is SignalistStatus.INACTIVE:
            await self.service.deactivate(row.id)  # type: ignore[arg-type]
        else:
            await self.service.activate(row.id)  # type: ignore[arg-type]
        release_archive.set()
        archived = await archive_task
        self.assertIs(archived.status, SignalistStatus.ARCHIVED)
        self.assertEqual(commits, 1)
        self.assertIs((await self.service.get_by_id(row.id)).status, SignalistStatus.ARCHIVED)  # type: ignore[arg-type]

    async def test_archive_retries_after_concurrent_deactivation(self) -> None:
        await self._assert_archive_retries_after_concurrent_transition(
            SignalistStatus.ACTIVE, SignalistStatus.INACTIVE
        )

    async def test_archive_retries_after_concurrent_activation(self) -> None:
        await self._assert_archive_retries_after_concurrent_transition(
            SignalistStatus.INACTIVE, SignalistStatus.ACTIVE
        )
