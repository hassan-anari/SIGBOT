from __future__ import annotations

import unittest
from pathlib import Path

from integration_tests.support import require_safe_test_database


class SQLiteIntegrationSafetyTests(unittest.TestCase):
    def test_helper_always_creates_its_own_isolated_file_target(self) -> None:
        safe = require_safe_test_database({"TEST_DATABASE_URL": "postgresql://must/not/be/used"})
        try:
            self.assertEqual(safe.url.drivername, "sqlite+aiosqlite")
            self.assertEqual(safe.path.parent, Path(safe.temporary_directory.name))
            self.assertFalse(safe.path.exists())
        finally:
            root = Path(safe.temporary_directory.name)
            safe.cleanup()
            self.assertFalse(root.exists())
