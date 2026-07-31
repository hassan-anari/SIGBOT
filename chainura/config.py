from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from chainura.errors import InvalidApplicationConfigurationError

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
    if url.drivername != "postgresql+psycopg":
        raise InvalidApplicationConfigurationError(
            "DATABASE_URL must use the postgresql+psycopg driver"
        )
    if not url.database:
        raise InvalidApplicationConfigurationError("DATABASE_URL must name a database")
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
        return candidate_telegram_user_id == self.main_admin_telegram_id


def load_application_settings() -> ApplicationSettings:
    """Explicitly load application settings from the real environment."""
    return ApplicationSettings.from_mapping(os.environ)
