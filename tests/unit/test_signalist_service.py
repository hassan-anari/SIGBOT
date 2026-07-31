from __future__ import annotations

import unittest

from chainura.application.signalist_service import SignalistService
from chainura.domain.signalist import SignalistStatus
from chainura.errors import (
    DuplicateTelegramUserIdError,
    InvalidSignalistInputError,
    InvalidSignalistStatusTransitionError,
    SignalistNotFoundError,
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
