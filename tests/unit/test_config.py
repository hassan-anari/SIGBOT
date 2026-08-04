from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chainura.config import ApplicationSettings
from chainura.environment import load_project_environment
from chainura.errors import InvalidApplicationConfigurationError


class EnvironmentLoadingTests(unittest.TestCase):
    def test_explicit_path_loads_and_real_environment_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text("DATABASE_URL=sqlite+aiosqlite:///./from-dotenv.db\nMAIN_ADMIN_TELEGRAM_ID=42\nSECRET_MARKER=hidden-value\n")
            with patch.dict(os.environ, {"MAIN_ADMIN_TELEGRAM_ID": "99"}, clear=True):
                self.assertTrue(load_project_environment(dotenv))
                self.assertEqual(os.environ["MAIN_ADMIN_TELEGRAM_ID"], "99")
                self.assertEqual(os.environ["SECRET_MARKER"], "hidden-value")

    def test_missing_file_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(load_project_environment(Path(directory) / "missing.env"))

    def test_disabled_is_respected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"; dotenv.write_text("DOTENV_ONLY=value\n")
            with patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1"}, clear=True):
                self.assertFalse(load_project_environment(dotenv))
                self.assertNotIn("DOTENV_ONLY", os.environ)

    def test_import_alone_does_not_load_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            import chainura.environment as environment
            importlib.reload(environment)
            self.assertNotIn("DATABASE_URL", os.environ)

    def test_market_parser_works_after_explicit_dotenv_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text("DATABASE_URL=sqlite+aiosqlite:///./temporary.db\nMAIN_ADMIN_TELEGRAM_ID=42\n")
            with patch.dict(os.environ, {}, clear=True):
                load_project_environment(dotenv)
                main = importlib.import_module("main")
                self.assertIsNotNone(main.build_parser().parse_args(["symbols"]))


class ConfigurationTests(unittest.TestCase):
    VALID = {"DATABASE_URL": "sqlite+aiosqlite:///./data/app.db", "MAIN_ADMIN_TELEGRAM_ID": "42"}

    def test_valid_explicit_mapping_identity_and_secret_safe_repr(self) -> None:
        settings = ApplicationSettings.from_mapping(self.VALID)
        self.assertTrue(settings.is_main_admin(42)); self.assertFalse(settings.is_main_admin(41))
        self.assertNotIn("app.db", repr(settings))

    def test_explicit_mapping_is_independent_from_environment(self) -> None:
        with patch.dict(os.environ, {"DATABASE_URL": "bad"}, clear=True):
            self.assertEqual(ApplicationSettings.from_mapping(self.VALID).main_admin_telegram_id, 42)

    def test_invalid_urls_are_safe(self) -> None:
        invalid = (
            "postgresql+psycopg://user:supersecret@localhost/app",
            "sqlite:///data/app.db", "sqlite+aiosqlite:///:memory:",
            "sqlite+aiosqlite:///", "not a url",
        )
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(InvalidApplicationConfigurationError) as caught:
                ApplicationSettings.from_mapping(dict(self.VALID, DATABASE_URL=url))
            self.assertNotIn("supersecret", str(caught.exception))

    def test_admin_validation_and_comparison(self) -> None:
        settings = ApplicationSettings.from_mapping(self.VALID)
        for candidate in (True, False, "42", 0, -1, 2**63):
            self.assertFalse(settings.is_main_admin(candidate))  # type: ignore[arg-type]
        for value in ("bad", "0", "-1", str(2**63)):
            with self.assertRaises(InvalidApplicationConfigurationError):
                ApplicationSettings.from_mapping(dict(self.VALID, MAIN_ADMIN_TELEGRAM_ID=value))

    def test_parent_directory_is_not_created_by_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "nested"
            ApplicationSettings.from_mapping({
                "DATABASE_URL": f"sqlite+aiosqlite:///{parent / 'db.sqlite'}",
                "MAIN_ADMIN_TELEGRAM_ID": "1",
            })
            self.assertFalse(parent.exists())


class TelegramRuntimeConfigurationTests(unittest.TestCase):
    def test_valid_secret_safe_runtime_settings(self) -> None:
        from chainura.config import TelegramRuntimeSettings
        settings = TelegramRuntimeSettings.from_mapping({
            "DATABASE_URL": "sqlite+aiosqlite:///./data/app.db",
            "MAIN_ADMIN_TELEGRAM_ID": "10", "TELEGRAM_BOT_TOKEN": "token-value",
            "REDIS_URL": "rediss://user:redis-secret@localhost:6380/1",
            "RENDER_TEMPLATE_ASSET_ROOT":"templates","RENDER_OUTPUT_ROOT":"output",
            "TOOBIT_COIN_LOGO_ROOT":"logos","RENDER_FONT_REGULAR_PATH":"regular.ttf",
            "RENDER_FONT_MEDIUM_PATH":"medium.ttf","RENDER_FONT_SEMIBOLD_PATH":"semibold.ttf",
            "RENDER_FONT_BOLD_PATH":"bold.ttf","RENDER_CONCURRENCY":"2",
        })
        rendered = repr(settings)
        self.assertNotIn("token-value", rendered); self.assertNotIn("redis-secret", rendered)

    def test_rendering_configuration_is_mandatory(self) -> None:
        from chainura.config import TelegramRuntimeSettings
        from chainura.errors import RenderingConfigurationError
        with self.assertRaises(RenderingConfigurationError):
            TelegramRuntimeSettings.from_mapping({
                "DATABASE_URL":"sqlite+aiosqlite:///./data/app.db",
                "MAIN_ADMIN_TELEGRAM_ID":"10","TELEGRAM_BOT_TOKEN":"token",
                "REDIS_URL":"redis://localhost:6379/1",
            })
