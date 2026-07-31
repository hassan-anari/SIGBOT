from __future__ import annotations

import importlib
import os
import unittest
from unittest.mock import patch

from chainura.config import ApplicationSettings
from chainura.errors import InvalidApplicationConfigurationError


class ConfigurationTests(unittest.TestCase):
    VALID = {"DATABASE_URL": "postgresql+psycopg://user:password@localhost/app",
             "MAIN_ADMIN_TELEGRAM_ID": "42"}

    def test_valid_explicit_mapping_and_identity(self) -> None:
        settings = ApplicationSettings.from_mapping(self.VALID)
        self.assertEqual(settings.main_admin_telegram_id, 42)
        self.assertTrue(settings.is_main_admin(42))
        self.assertFalse(settings.is_main_admin(41))
        self.assertNotIn("password", repr(settings))

    def test_missing_values(self) -> None:
        for name in self.VALID:
            values = dict(self.VALID); values.pop(name)
            with self.subTest(name=name), self.assertRaises(InvalidApplicationConfigurationError):
                ApplicationSettings.from_mapping(values)

    def test_invalid_admin_values(self) -> None:
        for value in ("bad", "0", "-1", str(2**63)):
            values = dict(self.VALID, MAIN_ADMIN_TELEGRAM_ID=value)
            with self.subTest(value=value), self.assertRaises(InvalidApplicationConfigurationError):
                ApplicationSettings.from_mapping(values)

    def test_invalid_url_is_secret_safe(self) -> None:
        for url in ("sqlite:///x", "postgresql://user:supersecret@localhost/app",
                    "postgresql+asyncpg://u:p@localhost/app", "not a url"):
            values = dict(self.VALID, DATABASE_URL=url)
            with self.subTest(url=url), self.assertRaises(InvalidApplicationConfigurationError) as caught:
                ApplicationSettings.from_mapping(values)
            self.assertNotIn("supersecret", str(caught.exception))

    def test_explicit_mapping_does_not_read_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ApplicationSettings.from_mapping(self.VALID).main_admin_telegram_id, 42)

    def test_market_imports_and_parser_need_no_settings(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            main = importlib.import_module("main")
            self.assertIsNotNone(main.build_parser())
