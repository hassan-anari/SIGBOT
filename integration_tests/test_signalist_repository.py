from __future__ import annotations

import unittest
from datetime import timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.errors import DuplicateTelegramUserIdError, SignalistPersistenceError
from chainura.persistence.database import create_session_factory
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from integration_tests.support import create_safe_engine, migrate, require_safe_test_database, truncate_signalists


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe = require_safe_test_database(); migrate("head", cls.safe)

    async def asyncSetUp(self) -> None:
        self.engine = create_safe_engine(self.safe); await truncate_signalists(self.engine, self.safe)
        self.sessions = create_session_factory(self.engine)

    async def asyncTearDown(self) -> None: await self.engine.dispose()

    async def create(self, telegram_id: int, name: str = "Name") -> Signalist:
        async with self.sessions() as session:
            await session.begin(); row = await SqlAlchemySignalistRepository(session).add(Signalist.create(telegram_id, name)); await session.commit(); return row

    async def test_crud_raw_lowercase_filter_and_separate_session(self) -> None:
        row = await self.create(100)
        self.assertGreater(row.id or 0, 0); self.assertEqual(row.created_at.utcoffset(), timezone.utc.utcoffset(None))  # type: ignore[union-attr]
        async with self.sessions() as session:
            repository = SqlAlchemySignalistRepository(session)
            found = await repository.get_by_id(row.id)  # type: ignore[arg-type]
            self.assertEqual(found.telegram_user_id, 100)  # type: ignore[union-attr]
            self.assertEqual((await repository.get_by_telegram_user_id(100)).id, row.id)  # type: ignore[union-attr]
            self.assertEqual(len(await repository.list(SignalistStatus.ACTIVE)), 1)
            raw = await session.scalar(text("SELECT status::text FROM signalists WHERE id=:id"), {"id": row.id})
            self.assertEqual(raw, "active"); self.assertEqual(raw, raw.lower())
            await session.commit()

    async def test_transitions_both_archive_paths_and_no_delete(self) -> None:
        active = await self.create(100); inactive_source = await self.create(200)
        async with self.sessions() as session:
            await session.begin(); repository = SqlAlchemySignalistRepository(session)
            inactive = await repository.persist_status(active.id, SignalistStatus.INACTIVE)  # type: ignore[arg-type]
            active_again = await repository.persist_status(inactive.id, SignalistStatus.ACTIVE)  # type: ignore[arg-type]
            archived_active = await repository.persist_status(active_again.id, SignalistStatus.ARCHIVED)  # type: ignore[arg-type]
            inactive_two = await repository.persist_status(inactive_source.id, SignalistStatus.INACTIVE)  # type: ignore[arg-type]
            archived_inactive = await repository.persist_status(inactive_two.id, SignalistStatus.ARCHIVED)  # type: ignore[arg-type]
            await session.commit()
        self.assertIsNotNone(archived_active.archived_at); self.assertIsNotNone(archived_inactive.archived_at)
        self.assertFalse(hasattr(SqlAlchemySignalistRepository, "delete"))

    async def test_named_duplicate_and_non_duplicate_constraints(self) -> None:
        await self.create(100)
        async with self.sessions() as session:
            await session.begin()
            with self.assertRaises(DuplicateTelegramUserIdError):
                await SqlAlchemySignalistRepository(session).add(Signalist.create(100, "Again"))
            await session.rollback()
        async with self.sessions() as session:
            await session.begin()
            with self.assertRaises(IntegrityError) as caught:
                await session.execute(text("INSERT INTO signalists (telegram_user_id,display_name) VALUES (0,'x')"))
            self.assertEqual(caught.exception.orig.diag.constraint_name, "ck_signalists_telegram_user_id_positive")
            await session.rollback()
        self.assertEqual((await self.create(200)).telegram_user_id, 200)

    async def test_display_and_archive_database_checks(self) -> None:
        for sql in ("INSERT INTO signalists (telegram_user_id,display_name) VALUES (1,'   ')",
                    "INSERT INTO signalists (telegram_user_id,display_name,status) VALUES (2,'x','archived')"):
            async with self.sessions() as session:
                await session.begin()
                with self.assertRaises(IntegrityError): await session.execute(text(sql))
                await session.rollback()
