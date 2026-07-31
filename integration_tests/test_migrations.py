from __future__ import annotations

import unittest

from sqlalchemy import text

from integration_tests.support import create_safe_engine, migrate, require_safe_test_database


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe = require_safe_test_database()
        migrate("base", cls.safe)

    async def test_upgrade_schema_enum_downgrade_and_reupgrade(self) -> None:
        migrate("head", self.safe)
        engine = create_safe_engine(self.safe)
        try:
            async with engine.connect() as connection:
                labels = (await connection.execute(text(
                    "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid "
                    "WHERE t.typname='signalist_status' ORDER BY e.enumsortorder"
                ))).scalars().all()
                self.assertEqual(labels, ["active", "inactive", "archived"])
                constraints = set((await connection.execute(text(
                    "SELECT conname FROM pg_constraint WHERE conrelid='signalists'::regclass"
                ))).scalars())
                self.assertTrue({"pk_signalists", "uq_signalists_telegram_user_id",
                    "ck_signalists_telegram_user_id_positive", "ck_signalists_display_name_nonempty",
                    "ck_signalists_archive_consistency"} <= constraints)
                indexes = set((await connection.execute(text(
                    "SELECT indexname FROM pg_indexes WHERE tablename='signalists'"
                ))).scalars())
                self.assertIn("ix_signalists_status", indexes)
        finally:
            await engine.dispose()
        migrate("base", self.safe)
        engine = create_safe_engine(self.safe)
        try:
            async with engine.connect() as connection:
                exists = await connection.scalar(text("SELECT to_regclass('signalists')"))
                enum_count = await connection.scalar(text("SELECT count(*) FROM pg_type WHERE typname='signalist_status'"))
                self.assertIsNone(exists); self.assertEqual(enum_count, 0)
        finally: await engine.dispose()
        migrate("head", self.safe)
