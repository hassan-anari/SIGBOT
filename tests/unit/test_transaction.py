from __future__ import annotations

import unittest

from sqlalchemy.exc import IntegrityError

from chainura.errors import DuplicateTelegramUserIdError, SignalistPersistenceError
from chainura.persistence.transaction import SqlAlchemySignalistTransaction


class Diagnostic:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


class DriverError(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__("driver detail must stay internal")
        self.diag = Diagnostic(constraint_name)


class InstrumentedSession:
    def __init__(self, commit_error: Exception | None = None) -> None:
        self.commit_error = commit_error
        self.begin_calls = self.commit_calls = self.rollback_calls = self.close_calls = 0
        self.active = False

    async def begin(self) -> None:
        self.begin_calls += 1; self.active = True

    async def commit(self) -> None:
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error
        self.active = False

    async def rollback(self) -> None:
        self.rollback_calls += 1; self.active = False

    async def close(self) -> None:
        self.close_calls += 1

    def in_transaction(self) -> bool:
        return self.active


class SessionFactory:
    def __init__(self, session: InstrumentedSession) -> None: self.session = session
    def __call__(self) -> InstrumentedSession: return self.session


class TransactionCommitFailureTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def integrity(constraint: str) -> IntegrityError:
        return IntegrityError("statement hidden by controlled error", {}, DriverError(constraint))

    async def assert_commit_translation(self, constraint: str, expected: type[Exception]) -> None:
        original = self.integrity(constraint)
        session = InstrumentedSession(original)
        transaction = SqlAlchemySignalistTransaction(SessionFactory(session))  # type: ignore[arg-type]
        with self.assertRaises(expected) as caught:
            async with transaction:
                await transaction.commit()
        self.assertIs(caught.exception.__cause__, original)
        self.assertEqual(session.commit_calls, 1)
        self.assertEqual(session.rollback_calls, 1)
        self.assertEqual(session.close_calls, 1)

    async def test_duplicate_commit_error_uses_named_constraint(self) -> None:
        await self.assert_commit_translation(
            "uq_signalists_telegram_user_id", DuplicateTelegramUserIdError
        )

    async def test_other_commit_integrity_error_is_persistence_error(self) -> None:
        await self.assert_commit_translation(
            "ck_signalists_display_name_nonempty", SignalistPersistenceError
        )

    async def test_new_transaction_succeeds_after_commit_failure(self) -> None:
        failed = InstrumentedSession(self.integrity("uq_signalists_telegram_user_id"))
        with self.assertRaises(DuplicateTelegramUserIdError):
            async with SqlAlchemySignalistTransaction(SessionFactory(failed)) as transaction:  # type: ignore[arg-type]
                await transaction.commit()
        clean = InstrumentedSession()
        async with SqlAlchemySignalistTransaction(SessionFactory(clean)) as transaction:  # type: ignore[arg-type]
            await transaction.commit()
        self.assertEqual((clean.commit_calls, clean.rollback_calls, clean.close_calls), (1, 0, 1))
