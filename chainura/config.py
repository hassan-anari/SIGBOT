from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from urllib.parse import urlsplit

from chainura.errors import InvalidApplicationConfigurationError
from chainura.rendering.production import RenderingSettings

MAX_SIGNED_BIGINT = 2**63 - 1


def _required(mapping: Mapping[str, str], name: str) -> str:
    value = mapping.get(name)
    if value is None or not value.strip():
        raise InvalidApplicationConfigurationError(f"{name} is required")
    return value.strip()


def validate_database_url(value: str) -> str:
    try:
        url = make_url(value)
    except (ArgumentError, ValueError, TypeError) as exc:
        raise InvalidApplicationConfigurationError("DATABASE_URL is invalid") from exc
    if url.drivername != "sqlite+aiosqlite":
        raise InvalidApplicationConfigurationError(
            "DATABASE_URL must use the sqlite+aiosqlite driver"
        )
    if not url.database or not url.database.strip("/."):
        raise InvalidApplicationConfigurationError("DATABASE_URL must name a database file")
    if url.database == ":memory:" or url.query.get("mode") == "memory":
        raise InvalidApplicationConfigurationError("DATABASE_URL must use a database file")
    return value


def parse_positive_bigint(value: str, name: str) -> int:
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as exc:
        raise InvalidApplicationConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0 or parsed > MAX_SIGNED_BIGINT:
        raise InvalidApplicationConfigurationError(
            f"{name} must be between 1 and {MAX_SIGNED_BIGINT}"
        )
    return parsed


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    database_url: str = field(repr=False)
    main_admin_telegram_id: int = field(repr=False)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "ApplicationSettings":
        database_url = validate_database_url(_required(mapping, "DATABASE_URL"))
        admin = parse_positive_bigint(
            _required(mapping, "MAIN_ADMIN_TELEGRAM_ID"),
            "MAIN_ADMIN_TELEGRAM_ID",
        )
        return cls(database_url, admin)

    def is_main_admin(self, candidate_telegram_user_id: int) -> bool:
        if isinstance(candidate_telegram_user_id, bool) or not isinstance(
            candidate_telegram_user_id, int
        ):
            return False
        return (
            0 < candidate_telegram_user_id <= MAX_SIGNED_BIGINT
            and candidate_telegram_user_id == self.main_admin_telegram_id
        )


def load_application_settings() -> ApplicationSettings:
    """Explicitly load application settings from the real environment."""
    return ApplicationSettings.from_mapping(os.environ)


def validate_redis_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError) as exc:
        raise InvalidApplicationConfigurationError("REDIS_URL is invalid") from exc
    if parsed.scheme not in {"redis", "rediss"}:
        raise InvalidApplicationConfigurationError("REDIS_URL must use redis or rediss")
    if not parsed.hostname:
        raise InvalidApplicationConfigurationError("REDIS_URL must name a host")
    return value


@dataclass(frozen=True, slots=True)
class TelegramRuntimeSettings:
    application: ApplicationSettings
    bot_token: str = field(repr=False)
    redis_url: str = field(repr=False)
    rendering: RenderingSettings | None = None

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "TelegramRuntimeSettings":
        application = ApplicationSettings.from_mapping(mapping)
        token = _required(mapping, "TELEGRAM_BOT_TOKEN")
        if any(ord(character) < 32 for character in token):
            raise InvalidApplicationConfigurationError("TELEGRAM_BOT_TOKEN is invalid")
        redis_url = validate_redis_url(_required(mapping, "REDIS_URL"))
        render_keys=("RENDER_TEMPLATE_ASSET_ROOT","RENDER_OUTPUT_ROOT","TOOBIT_COIN_LOGO_ROOT",
          "RENDER_FONT_REGULAR_PATH","RENDER_FONT_MEDIUM_PATH","RENDER_FONT_SEMIBOLD_PATH","RENDER_FONT_BOLD_PATH","RENDER_CONCURRENCY")
        rendering=RenderingSettings.from_mapping(mapping) if any(key in mapping for key in render_keys) else None
        return cls(application=application, bot_token=token, redis_url=redis_url,rendering=rendering)


def load_telegram_runtime_settings() -> TelegramRuntimeSettings:
    return TelegramRuntimeSettings.from_mapping(os.environ)
