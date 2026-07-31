from __future__ import annotations

import unittest

from chainura.application.signalist_service import SignalistService
from chainura.domain.signalist import SignalistStatus
from chainura.errors import (
    DuplicateTelegramUserIdError,
    InvalidSignalistInputError,
    InvalidSignalistStatusTransitionError,
    SignalistNotFoundError,
    SignalistPersistenceError,
)
from tests.unit.fakes import FakeRepository, FakeTransactionFactory


class SignalistServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = FakeRepository()
        self.factory = FakeTransactionFactory(self.repository)
        self.service = SignalistService(self.factory)

    async def test_create_get_list_and_filter(self) -> None:
        first = await self.service.create(100, "First")
        second = await self.service.create(200, "Second")
        self.assertEqual((await self.service.get_by_id(first.id)).telegram_user_id, 100)  # type: ignore[arg-type]
        self.assertEqual((await self.service.get_by_telegram_user_id(200)).id, second.id)
        self.assertEqual(len(await self.service.list()), 2)
        self.assertEqual(len(await self.service.list(SignalistStatus.ACTIVE)), 2)
        self.assertTrue(all(t.commits == 1 and t.closed for t in self.factory.created))

    async def test_transitions_and_original_is_unchanged(self) -> None:
        original = await self.service.create(100, "First")
        inactive = await self.service.deactivate(original.id)  # type: ignore[arg-type]
        self.assertIs(original.status, SignalistStatus.ACTIVE)
        active = await self.service.activate(inactive.id)  # type: ignore[arg-type]
        archived = await self.service.archive(active.id)  # type: ignore[arg-type]
        self.assertIs(archived.status, SignalistStatus.ARCHIVED)
        self.assertIsNotNone(archived.archived_at)

    async def test_archive_inactive_and_reject_archived(self) -> None:
        row = await self.service.create(100, "First")
        row = await self.service.deactivate(row.id)  # type: ignore[arg-type]
        row = await self.service.archive(row.id)  # type: ignore[arg-type]
        with self.assertRaises(InvalidSignalistStatusTransitionError):
            await self.service.activate(row.id)  # type: ignore[arg-type]
        self.assertEqual(self.factory.created[-1].commits, 0)
        self.assertEqual(self.factory.created[-1].rollbacks, 1)

    async def test_duplicate_and_not_found_rollback(self) -> None:
        await self.service.create(100, "First")
        with self.assertRaises(DuplicateTelegramUserIdError): await self.service.create(100, "Again")
        with self.assertRaises(SignalistNotFoundError): await self.service.get_by_id(999)
        self.assertTrue(all(t.closed for t in self.factory.created))
        self.assertEqual(self.factory.created[-1].commits, 0)

    async def test_repository_and_commit_failure_close_and_rollback(self) -> None:
        self.repository.fail = RuntimeError("repository failed")
        with self.assertRaises(RuntimeError): await self.service.get_by_id(1)
        self.assertEqual((self.factory.created[-1].rollbacks, self.factory.created[-1].commits), (1, 0))
        repository = FakeRepository(); factory = FakeTransactionFactory(repository, fail_commit=True)
        with self.assertRaises(RuntimeError): await SignalistService(factory).create(1, "x")
        self.assertTrue(factory.created[-1].closed)
        self.assertEqual(factory.created[-1].rollbacks, 1)

    async def test_invalid_internal_ids_open_no_transaction(self) -> None:
        operations = (self.service.get_by_id, self.service.activate,
                      self.service.deactivate, self.service.archive)
        for operation in operations:
            for value in (True, False, 0, -1, 2**63, "1", 1.5):
                before = len(self.factory.created)
                with self.subTest(operation=operation.__name__, value=value), self.assertRaises(InvalidSignalistInputError):
                    await operation(value)  # type: ignore[arg-type]
                self.assertEqual(len(self.factory.created), before)

    async def test_invalid_telegram_lookup_and_status_open_no_transaction(self) -> None:
        for value in (True, False, 0, -1, 2**63, "1", 1.5):
            before = len(self.factory.created)
            with self.subTest(value=value), self.assertRaises(InvalidSignalistInputError):
                await self.service.get_by_telegram_user_id(value)  # type: ignore[arg-type]
            self.assertEqual(len(self.factory.created), before)
        for status in ("active", 1, True):
            before = len(self.factory.created)
            with self.subTest(status=status), self.assertRaises(InvalidSignalistInputError):
                await self.service.list(status)  # type: ignore[arg-type]
            self.assertEqual(len(self.factory.created), before)

class CompareAndSwapServiceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def replace_status(repository, signalist_id, status):
        from datetime import datetime, timezone
        from chainura.domain.signalist import Signalist

        current = repository.rows[signalist_id]
        now = datetime.now(timezone.utc)
        repository.rows[signalist_id] = Signalist.rehydrate(
            id=current.id, telegram_user_id=current.telegram_user_id,
            display_name=current.display_name, telegram_username=current.telegram_username,
            status=status, created_at=current.created_at, updated_at=now,
            archived_at=now if status is SignalistStatus.ARCHIVED else None,
        )

    async def test_stale_transition_uses_latest_state_and_rejects(self) -> None:
        repository = FakeRepository()
        service = SignalistService(FakeTransactionFactory(repository))
        row = await service.create(700, "CAS")
        row = await service.deactivate(row.id)  # type: ignore[arg-type]
        original = repository.compare_and_swap_status

        async def archive_before_stale(signalist_id, expected, target):
            current = repository.rows[signalist_id]
            repository.rows[signalist_id] = Signalist.rehydrate(
                id=current.id, telegram_user_id=current.telegram_user_id,
                display_name=current.display_name, telegram_username=current.telegram_username,
                status=SignalistStatus.ARCHIVED, created_at=current.created_at,
                updated_at=current.updated_at, archived_at=current.updated_at,
            )
            return await original(signalist_id, expected, target)

        from chainura.domain.signalist import Signalist
        repository.compare_and_swap_status = archive_before_stale  # type: ignore[method-assign]
        with self.assertRaises(InvalidSignalistStatusTransitionError):
            await service.activate(row.id)  # type: ignore[arg-type]

    async def test_archive_retries_after_concurrent_deactivation(self) -> None:
        repository = FakeRepository(); factory = FakeTransactionFactory(repository)
        service = SignalistService(factory)
        row = await service.create(701, "Retry deactivate")
        original = repository.compare_and_swap_status
        calls = 0

        async def concurrent_deactivation(signalist_id, expected, target):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.replace_status(repository, signalist_id, SignalistStatus.INACTIVE)
            return await original(signalist_id, expected, target)

        repository.compare_and_swap_status = concurrent_deactivation  # type: ignore[method-assign]
        archived = await service.archive(row.id)  # type: ignore[arg-type]
        self.assertIs(archived.status, SignalistStatus.ARCHIVED)
        self.assertEqual(calls, 2)
        self.assertEqual(factory.created[-1].commits, 1)

    async def test_archive_retries_after_concurrent_activation(self) -> None:
        repository = FakeRepository(); factory = FakeTransactionFactory(repository)
        service = SignalistService(factory)
        row = await service.create(702, "Retry activate")
        row = await service.deactivate(row.id)  # type: ignore[arg-type]
        original = repository.compare_and_swap_status
        calls = 0

        async def concurrent_activation(signalist_id, expected, target):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.replace_status(repository, signalist_id, SignalistStatus.ACTIVE)
            return await original(signalist_id, expected, target)

        repository.compare_and_swap_status = concurrent_activation  # type: ignore[method-assign]
        archived = await service.archive(row.id)  # type: ignore[arg-type]
        self.assertIs(archived.status, SignalistStatus.ARCHIVED)
        self.assertEqual(calls, 2)
        self.assertEqual(factory.created[-1].commits, 1)

    async def test_repeated_contention_is_bounded_and_controlled(self) -> None:
        repository = FakeRepository(); factory = FakeTransactionFactory(repository)
        service = SignalistService(factory)
        row = await service.create(703, "Contention")
        calls = 0

        async def always_stale(signalist_id, expected, target):
            nonlocal calls
            calls += 1
            replacement = (
                SignalistStatus.INACTIVE
                if repository.rows[signalist_id].status is SignalistStatus.ACTIVE
                else SignalistStatus.ACTIVE
            )
            self.replace_status(repository, signalist_id, replacement)
            return None

        repository.compare_and_swap_status = always_stale  # type: ignore[method-assign]
        with self.assertRaises(SignalistPersistenceError) as caught:
            await service.archive(row.id)  # type: ignore[arg-type]
        self.assertNotIsInstance(caught.exception, RuntimeError)
        self.assertEqual(calls, 3)
        transition = factory.created[-1]
        self.assertEqual(transition.commits, 0)
        self.assertEqual(transition.rollbacks, 1)
        self.assertTrue(transition.closed)
