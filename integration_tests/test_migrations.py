from __future__ import annotations

import unittest

from sqlalchemy import text

from integration_tests.support import create_safe_engine, migrate, require_safe_test_database


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe = require_safe_test_database()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.safe.cleanup()

    async def test_empty_upgrade_downgrade_and_reupgrade(self) -> None:
        migrate("head", self.safe)

    async def test_phase4_downgrade_preserves_signalist(self) -> None:
        migrate("20260731_01", self.safe)
        engine = create_safe_engine(self.safe)
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO signalists (telegram_user_id,display_name) VALUES (123,'existing')"))
        await engine.dispose()
        migrate("head", self.safe)
        engine = create_safe_engine(self.safe)
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO destinations (internal_name,telegram_chat_id,destination_type,access_level,daily_limit) VALUES ('d',-1,'channel','private',1)"))
        await engine.dispose()
        migrate("-4", self.safe)
        engine = create_safe_engine(self.safe)
        async with engine.connect() as connection:
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signalists WHERE telegram_user_id=123")),1)
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='destinations'")),0)
        await engine.dispose(); migrate("head", self.safe)
        engine = create_safe_engine(self.safe)
        try:
            async with engine.connect() as connection:
                table_sql = await connection.scalar(text(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='signalists'"
                ))
                self.assertIn("signalist_status", table_sql)
                self.assertIn("uq_signalists_telegram_user_id", table_sql)
                indexes = set((await connection.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='signalists'"
                ))).scalars())
                self.assertIn("ix_signalists_status", indexes)
        finally:
            await engine.dispose()
        migrate("base", self.safe)
        engine = create_safe_engine(self.safe)
        try:
            async with engine.connect() as connection:
                exists = await connection.scalar(text(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='signalists'"
                ))
                self.assertEqual(exists, 0)
        finally:
            await engine.dispose()
        migrate("head", self.safe)
