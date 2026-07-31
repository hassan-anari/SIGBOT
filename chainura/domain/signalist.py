from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from chainura.errors import (
    InvalidSignalistInputError,
    InvalidSignalistStatusTransitionError,
)

MAX_SIGNED_BIGINT = 2**63 - 1


class SignalistStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


def validate_telegram_user_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSignalistInputError("telegram_user_id must be an integer")
    if value <= 0 or value > MAX_SIGNED_BIGINT:
        raise InvalidSignalistInputError("telegram_user_id must be a positive 64-bit integer")
    return value


def validate_internal_signalist_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidSignalistInputError("signalist_id must be an integer")
    if value <= 0 or value > MAX_SIGNED_BIGINT:
        raise InvalidSignalistInputError("signalist_id must be a positive 64-bit integer")
    return value


def _display_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidSignalistInputError("display_name must not be empty")
    return value.strip()


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidSignalistInputError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Signalist:
    id: int | None
    telegram_user_id: int
    display_name: str
    telegram_username: str | None
    status: SignalistStatus
    created_at: datetime | None
    updated_at: datetime | None
    archived_at: datetime | None

    @classmethod
    def create(
        cls,
        telegram_user_id: int,
        display_name: str,
        telegram_username: str | None = None,
    ) -> "Signalist":
        if telegram_username is not None and not isinstance(telegram_username, str):
            raise InvalidSignalistInputError("telegram_username must be a string or None")
        return cls(
            id=None,
            telegram_user_id=validate_telegram_user_id(telegram_user_id),
            display_name=_display_name(display_name),
            telegram_username=telegram_username,
            status=SignalistStatus.ACTIVE,
            created_at=None,
            updated_at=None,
            archived_at=None,
        )

    @classmethod
    def rehydrate(
        cls,
        *,
        id: int,
        telegram_user_id: int,
        display_name: str,
        telegram_username: str | None,
        status: SignalistStatus,
        created_at: datetime,
        updated_at: datetime,
        archived_at: datetime | None,
    ) -> "Signalist":
        if isinstance(id, bool) or not isinstance(id, int) or id <= 0:
            raise InvalidSignalistInputError("id must be a positive integer")
        if not isinstance(status, SignalistStatus):
            raise InvalidSignalistInputError("status is invalid")
        if telegram_username is not None and not isinstance(telegram_username, str):
            raise InvalidSignalistInputError("telegram_username must be a string or None")
        created = _aware_utc(created_at, "created_at")
        updated = _aware_utc(updated_at, "updated_at")
        archived = _aware_utc(archived_at, "archived_at") if archived_at else None
        if (status is SignalistStatus.ARCHIVED) != (archived is not None):
            raise InvalidSignalistInputError("status and archived_at are inconsistent")
        return cls(id, validate_telegram_user_id(telegram_user_id), _display_name(display_name),
                   telegram_username, status, created, updated, archived)

    def approve_transition(self, target: SignalistStatus) -> SignalistStatus:
        allowed = {
            (SignalistStatus.ACTIVE, SignalistStatus.INACTIVE),
            (SignalistStatus.INACTIVE, SignalistStatus.ACTIVE),
            (SignalistStatus.ACTIVE, SignalistStatus.ARCHIVED),
            (SignalistStatus.INACTIVE, SignalistStatus.ARCHIVED),
        }
        if (self.status, target) not in allowed:
            raise InvalidSignalistStatusTransitionError(
                f"transition from {self.status.value} to {target.value} is not allowed"
            )
        return target

    def approve_activation(self) -> SignalistStatus:
        return self.approve_transition(SignalistStatus.ACTIVE)

    def approve_deactivation(self) -> SignalistStatus:
        return self.approve_transition(SignalistStatus.INACTIVE)

    def approve_archive(self) -> SignalistStatus:
        return self.approve_transition(SignalistStatus.ARCHIVED)
