from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from chainura.errors import SignalistPersistenceError


class UTCDateTime(TypeDecorator[datetime]):
    """Persist aware UTC datetimes as deterministic ISO-8601 text."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise SignalistPersistenceError("timestamp must be timezone-aware UTC")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    def process_result_value(self, value: str | datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
