from __future__ import annotations

import unittest
from datetime import timezone

from sqlalchemy import text

from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.errors import DuplicateTelegramUserIdError
from chainura.persistence.database import create_session_factory
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.models import SignalistRecord
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

    async def test_fresh_read_replaces_strong_identity_map_state(self) -> None:
        row = await self.create(500)
        async with self.sessions() as stale_session:
            strong_record = await stale_session.get(SignalistRecord, row.id)
            self.assertIs(strong_record.status, SignalistStatus.ACTIVE)  # type: ignore[union-attr]
            await stale_session.commit()

            async with self.sessions() as writer:
                await writer.begin()
                updated = await SqlAlchemySignalistRepository(writer).compare_and_swap_status(
                    row.id, SignalistStatus.ACTIVE, SignalistStatus.INACTIVE  # type: ignore[arg-type]
                )
                await writer.commit()
            self.assertIs(updated.status, SignalistStatus.INACTIVE)  # type: ignore[union-attr]
            self.assertIs(strong_record.status, SignalistStatus.ACTIVE)  # type: ignore[union-attr]

            fresh = await SqlAlchemySignalistRepository(stale_session).get_by_id_fresh(row.id)  # type: ignore[arg-type]
            self.assertIs(fresh.status, SignalistStatus.INACTIVE)  # type: ignore[union-attr]
            self.assertIs(strong_record.status, SignalistStatus.INACTIVE)  # type: ignore[union-attr]

    async def test_successful_cas_returns_fresh_utc_persisted_values(self) -> None:
        row = await self.create(600, "Persisted fields")
        async with self.sessions() as session:
            repository = SqlAlchemySignalistRepository(session)
            strong_record = await session.get(SignalistRecord, row.id)
            previous_updated_at = row.updated_at
            inactive = await repository.compare_and_swap_status(
                row.id, SignalistStatus.ACTIVE, SignalistStatus.INACTIVE  # type: ignore[arg-type]
            )
            self.assertIs(inactive.status, SignalistStatus.INACTIVE)  # type: ignore[union-attr]
            self.assertEqual(inactive.display_name, "Persisted fields")  # type: ignore[union-attr]
            self.assertIsNone(inactive.archived_at)  # type: ignore[union-attr]
            self.assertEqual(inactive.updated_at.tzinfo, timezone.utc)  # type: ignore[union-attr]
            self.assertGreater(inactive.updated_at, previous_updated_at)  # type: ignore[arg-type,union-attr]
            self.assertIs(strong_record.status, SignalistStatus.INACTIVE)  # type: ignore[union-attr]
            archived = await repository.compare_and_swap_status(
                row.id, SignalistStatus.INACTIVE, SignalistStatus.ARCHIVED  # type: ignore[arg-type]
            )
            self.assertIs(archived.status, SignalistStatus.ARCHIVED)  # type: ignore[union-attr]
            self.assertEqual(archived.archived_at.tzinfo, timezone.utc)  # type: ignore[union-attr]
            self.assertEqual(archived.archived_at, archived.updated_at)  # type: ignore[union-attr]
            await session.commit()
