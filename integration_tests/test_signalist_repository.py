from __future__ import annotations

import unittest
from datetime import timezone

from sqlalchemy import text

from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.errors import DuplicateTelegramUserIdError
from chainura.persistence.database import create_session_factory
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from integration_tests.support import create_safe_engine, migrate, require_safe_test_database, truncate_signalists


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe = require_safe_test_database(); migrate("head", cls.safe)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.safe.cleanup()

    async def asyncSetUp(self) -> None:
        self.engine = create_safe_engine(self.safe); await truncate_signalists(self.engine, self.safe)
        self.sessions = create_session_factory(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def create(self, telegram_id: int, name: str = "Name") -> Signalist:
        async with self.sessions() as session:
            await session.begin()
            row = await SqlAlchemySignalistRepository(session).add(Signalist.create(telegram_id, name))
            await session.commit()
            return row

    async def test_create_get_list_filter_and_utc(self) -> None:
        row = await self.create(100)
        self.assertGreater(row.id or 0, 0)
        self.assertEqual(row.created_at.tzinfo, timezone.utc)  # type: ignore[union-attr]
        async with self.sessions() as session:
            repository = SqlAlchemySignalistRepository(session)
            self.assertEqual((await repository.get_by_id(row.id)).telegram_user_id, 100)  # type: ignore[arg-type,union-attr]
            self.assertEqual((await repository.get_by_telegram_user_id(100)).id, row.id)  # type: ignore[union-attr]
            self.assertEqual(len(await repository.list(SignalistStatus.ACTIVE)), 1)

    async def test_unique_telegram_id_and_no_hard_delete(self) -> None:
        await self.create(100)
        async with self.sessions() as session:
            await session.begin()
            with self.assertRaises(DuplicateTelegramUserIdError):
                await SqlAlchemySignalistRepository(session).add(Signalist.create(100, "Again"))
        self.assertFalse(hasattr(SqlAlchemySignalistRepository, "delete"))

    async def test_pragmas_and_archive_consistency(self) -> None:
        async with self.engine.connect() as connection:
            self.assertEqual(await connection.scalar(text("PRAGMA foreign_keys")), 1)
            self.assertEqual(await connection.scalar(text("PRAGMA busy_timeout")), 5000)
            self.assertEqual((await connection.scalar(text("PRAGMA journal_mode"))).lower(), "wal")
            self.assertEqual(await connection.scalar(text("PRAGMA synchronous")), 1)
            for sql in (
                "INSERT INTO signalists (telegram_user_id,display_name) VALUES (0,'x')",
                "INSERT INTO signalists (telegram_user_id,display_name) VALUES (1,'   ')",
                "INSERT INTO signalists (telegram_user_id,display_name,status) VALUES (2,'x','archived')",
            ):
                with self.assertRaises(Exception):
                    await connection.execute(text(sql))
                await connection.rollback()

    async def test_persistence_across_engine_recreation(self) -> None:
        row = await self.create(400)
        await self.engine.dispose()
        self.engine = create_safe_engine(self.safe)
        sessions = create_session_factory(self.engine)
        async with sessions() as session:
            persisted = await SqlAlchemySignalistRepository(session).get_by_id(row.id)  # type: ignore[arg-type]
            self.assertEqual(persisted.telegram_user_id, 400)  # type: ignore[union-attr]
