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
