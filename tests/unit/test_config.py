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

    def test_main_admin_comparison_rejects_invalid_integer_like_values(self) -> None:
        settings = ApplicationSettings.from_mapping(self.VALID)
        self.assertTrue(settings.is_main_admin(42))
        self.assertFalse(settings.is_main_admin(41))
        for candidate in (True, False, "42", 0, -1, 2**63):
            with self.subTest(candidate=candidate):
                self.assertFalse(settings.is_main_admin(candidate))  # type: ignore[arg-type]

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

class TelegramRuntimeConfigurationTests(unittest.TestCase):
    def test_valid_secret_safe_runtime_settings(self) -> None:
        from chainura.config import TelegramRuntimeSettings
        settings = TelegramRuntimeSettings.from_mapping({
            "DATABASE_URL": "postgresql+psycopg://user:secret@localhost/app",
            "MAIN_ADMIN_TELEGRAM_ID": "10",
            "TELEGRAM_BOT_TOKEN": "token-value",
            "REDIS_URL": "rediss://user:redis-secret@localhost:6380/1",
        })
        rendered = repr(settings)
        self.assertNotIn("token-value", rendered)
        self.assertNotIn("redis-secret", rendered)

    def test_missing_token_and_invalid_redis_are_safe(self) -> None:
        from chainura.config import TelegramRuntimeSettings
        from chainura.errors import InvalidApplicationConfigurationError
        base = {"DATABASE_URL": "postgresql+psycopg://u:p@localhost/app",
                "MAIN_ADMIN_TELEGRAM_ID": "10", "REDIS_URL": "redis://localhost/0"}
        with self.assertRaisesRegex(InvalidApplicationConfigurationError, "TELEGRAM_BOT_TOKEN"):
            TelegramRuntimeSettings.from_mapping(base)
        base["TELEGRAM_BOT_TOKEN"] = "x"
        base["REDIS_URL"] = "http://user:top-secret@localhost/1"
        with self.assertRaises(InvalidApplicationConfigurationError) as caught:
            TelegramRuntimeSettings.from_mapping(base)
        self.assertNotIn("top-secret", str(caught.exception))
