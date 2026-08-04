from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
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
    publication_concurrency: int = 3
    send_max_attempts: int = 3
    send_retry_base_seconds: float = 0.5
    publication_sweep_seconds: float = 5.0
    update_publication_concurrency: int = 3
    update_send_max_attempts: int = 3
    update_send_retry_base_seconds: float = 0.5
    update_publication_sweep_seconds: float = 5.0
    monitoring_enabled: bool = True
    monitoring_interval_seconds: float = 5.0
    monitoring_concurrency: int = 8
    monitoring_batch_size: int = 100
    breakeven_min_open_seconds: int = 300
    breakeven_favorable_move_percent: Decimal = Decimal("1.00")
    breakeven_execution_tolerance_percent: Decimal = Decimal("0.10")
    breakeven_rearm_distance_percent: Decimal = Decimal("0.20")
    breakeven_prompt_cooldown_seconds: int = 900
    signalist_notification_concurrency: int = 3
    signalist_notification_max_attempts: int = 3
    signalist_notification_retry_base_seconds: float = .5
    signalist_notification_sweep_seconds: float = 5

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "TelegramRuntimeSettings":
        application = ApplicationSettings.from_mapping(mapping)
        token = _required(mapping, "TELEGRAM_BOT_TOKEN")
        if any(ord(character) < 32 for character in token):
            raise InvalidApplicationConfigurationError("TELEGRAM_BOT_TOKEN is invalid")
        redis_url = validate_redis_url(_required(mapping, "REDIS_URL"))
        render_keys=("RENDER_TEMPLATE_ASSET_ROOT","RENDER_OUTPUT_ROOT","TOOBIT_COIN_LOGO_ROOT",
          "RENDER_FONT_REGULAR_PATH","RENDER_FONT_MEDIUM_PATH","RENDER_FONT_SEMIBOLD_PATH","RENDER_FONT_BOLD_PATH","RENDER_CONCURRENCY")
        rendering=RenderingSettings.from_mapping(mapping)
        try:
            concurrency=int(mapping.get("TELEGRAM_PUBLICATION_CONCURRENCY","3"))
            attempts=int(mapping.get("TELEGRAM_SEND_MAX_ATTEMPTS","3"))
            retry=float(mapping.get("TELEGRAM_SEND_RETRY_BASE_SECONDS","0.5"))
            sweep=float(mapping.get("PUBLICATION_SWEEP_SECONDS","5"))
            upd_concurrency=int(mapping.get("UPDATE_PUBLICATION_CONCURRENCY","3"));upd_attempts=int(mapping.get("UPDATE_SEND_MAX_ATTEMPTS","3"))
            upd_retry=float(mapping.get("UPDATE_SEND_RETRY_BASE_SECONDS","0.5"));upd_sweep=float(mapping.get("UPDATE_PUBLICATION_SWEEP_SECONDS","5"))
        except ValueError as exc:raise InvalidApplicationConfigurationError("publication settings must be numeric") from exc
        if not 1<=concurrency<=20:raise InvalidApplicationConfigurationError("TELEGRAM_PUBLICATION_CONCURRENCY must be between 1 and 20")
        if not 1<=attempts<=10:raise InvalidApplicationConfigurationError("TELEGRAM_SEND_MAX_ATTEMPTS must be between 1 and 10")
        if not 0.1<=retry<=60:raise InvalidApplicationConfigurationError("TELEGRAM_SEND_RETRY_BASE_SECONDS must be between 0.1 and 60")
        if not 0.5<=sweep<=300:raise InvalidApplicationConfigurationError("PUBLICATION_SWEEP_SECONDS must be between 0.5 and 300")
        if not 1<=upd_concurrency<=20:raise InvalidApplicationConfigurationError("UPDATE_PUBLICATION_CONCURRENCY must be between 1 and 20")
        if not 1<=upd_attempts<=10:raise InvalidApplicationConfigurationError("UPDATE_SEND_MAX_ATTEMPTS must be between 1 and 10")
        if not 0.1<=upd_retry<=60:raise InvalidApplicationConfigurationError("UPDATE_SEND_RETRY_BASE_SECONDS must be between 0.1 and 60")
        if not 0.5<=upd_sweep<=300:raise InvalidApplicationConfigurationError("UPDATE_PUBLICATION_SWEEP_SECONDS must be between 0.5 and 300")
        enabled=mapping.get("MONITORING_ENABLED","true").strip().lower()
        if enabled not in ("true","false"):raise InvalidApplicationConfigurationError("MONITORING_ENABLED must be true or false")
        try:
            mi=float(mapping.get("MONITORING_INTERVAL_SECONDS","5"));mc=int(mapping.get("MONITORING_CONCURRENCY","8"));mb=int(mapping.get("MONITORING_BATCH_SIZE","100"))
            age=int(mapping.get("BREAKEVEN_MIN_OPEN_SECONDS","300"));fav=Decimal(mapping.get("BREAKEVEN_FAVORABLE_MOVE_PERCENT","1.00"));tol=Decimal(mapping.get("BREAKEVEN_EXECUTION_TOLERANCE_PERCENT","0.10"));rearm=Decimal(mapping.get("BREAKEVEN_REARM_DISTANCE_PERCENT","0.20"));cool=int(mapping.get("BREAKEVEN_PROMPT_COOLDOWN_SECONDS","900"))
            nc=int(mapping.get("SIGNALIST_NOTIFICATION_CONCURRENCY","3"));na=int(mapping.get("SIGNALIST_NOTIFICATION_MAX_ATTEMPTS","3"));nr=float(mapping.get("SIGNALIST_NOTIFICATION_RETRY_BASE_SECONDS","0.5"));ns=float(mapping.get("SIGNALIST_NOTIFICATION_SWEEP_SECONDS","5"))
        except (ValueError,InvalidOperation) as exc:raise InvalidApplicationConfigurationError("Phase 6A settings must be numeric") from exc
        if not .5<=mi<=300 or not 1<=mc<=32 or not 1<=mb<=1000:raise InvalidApplicationConfigurationError("monitoring settings are out of range")
        if age<0 or cool<0 or any(not x.is_finite() or x<=0 for x in (fav,tol,rearm)) or not tol<rearm or not fav>tol:raise InvalidApplicationConfigurationError("Breakeven settings are inconsistent")
        if not 1<=nc<=20 or not 1<=na<=10 or not .1<=nr<=60 or not .5<=ns<=300:raise InvalidApplicationConfigurationError("Signalist notification settings are out of range")
        return cls(application=application, bot_token=token, redis_url=redis_url,rendering=rendering,
            publication_concurrency=concurrency,send_max_attempts=attempts,
            send_retry_base_seconds=retry,publication_sweep_seconds=sweep,
            update_publication_concurrency=upd_concurrency,update_send_max_attempts=upd_attempts,
            update_send_retry_base_seconds=upd_retry,update_publication_sweep_seconds=upd_sweep,
            monitoring_enabled=enabled=="true",monitoring_interval_seconds=mi,monitoring_concurrency=mc,monitoring_batch_size=mb,
            breakeven_min_open_seconds=age,breakeven_favorable_move_percent=fav,breakeven_execution_tolerance_percent=tol,breakeven_rearm_distance_percent=rearm,breakeven_prompt_cooldown_seconds=cool,
            signalist_notification_concurrency=nc,signalist_notification_max_attempts=na,signalist_notification_retry_base_seconds=nr,signalist_notification_sweep_seconds=ns)


def load_telegram_runtime_settings() -> TelegramRuntimeSettings:
    return TelegramRuntimeSettings.from_mapping(os.environ)
