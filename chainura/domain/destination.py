from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum

from chainura.errors import (InvalidDestinationInputError,
                              InvalidDestinationStatusTransitionError,
                              InvalidExchangePriorityError)

MAX_SIGNED_BIGINT = 2**63 - 1


class DestinationStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class DestinationType(str, Enum):
    CHANNEL = "channel"
    GROUP = "group"


class DestinationAccessLevel(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class Exchange(str, Enum):
    OURBIT = "ourbit"
    TOOBIT = "toobit"
    LBANK = "lbank"


def _integer(value: object, name: str, *, nonzero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidDestinationInputError(f"{name} must be an integer")
    minimum = -MAX_SIGNED_BIGINT - 1 if nonzero else 1
    if value < minimum or value > MAX_SIGNED_BIGINT or (nonzero and value == 0):
        raise InvalidDestinationInputError(f"{name} is outside the supported range")
    return value


def _name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidDestinationInputError("internal_name must not be empty")
    return value.strip()


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidDestinationInputError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Destination:
    id: int | None
    internal_name: str
    telegram_chat_id: int
    destination_type: DestinationType
    access_level: DestinationAccessLevel
    status: DestinationStatus
    daily_limit: int
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def create(cls, internal_name: str, telegram_chat_id: int,
               destination_type: DestinationType, access_level: DestinationAccessLevel,
               daily_limit: int) -> "Destination":
        return cls._validated(None, internal_name, telegram_chat_id, destination_type,
                              access_level, DestinationStatus.ACTIVE, daily_limit, None, None)

    @classmethod
    def rehydrate(cls, *, id: int, internal_name: str, telegram_chat_id: int,
                  destination_type: DestinationType, access_level: DestinationAccessLevel,
                  status: DestinationStatus, daily_limit: int, created_at: datetime,
                  updated_at: datetime) -> "Destination":
        return cls._validated(id, internal_name, telegram_chat_id, destination_type,
                              access_level, status, daily_limit, created_at, updated_at)

    @classmethod
    def _validated(cls, id: int | None, internal_name: str, telegram_chat_id: int,
                   destination_type: DestinationType, access_level: DestinationAccessLevel,
                   status: DestinationStatus, daily_limit: int, created_at: datetime | None,
                   updated_at: datetime | None) -> "Destination":
        if id is not None: _integer(id, "id")
        if not isinstance(destination_type, DestinationType): raise InvalidDestinationInputError("destination_type is invalid")
        if not isinstance(access_level, DestinationAccessLevel): raise InvalidDestinationInputError("access_level is invalid")
        if not isinstance(status, DestinationStatus): raise InvalidDestinationInputError("status is invalid")
        _integer(telegram_chat_id, "telegram_chat_id", nonzero=True)
        _integer(daily_limit, "daily_limit")
        if (created_at is None) != (updated_at is None): raise InvalidDestinationInputError("timestamps are inconsistent")
        return cls(id, _name(internal_name), telegram_chat_id, destination_type, access_level,
                   status, daily_limit, _utc(created_at, "created_at") if created_at else None,
                   _utc(updated_at, "updated_at") if updated_at else None)

    def with_metadata(self, internal_name: str, destination_type: DestinationType,
                      access_level: DestinationAccessLevel, daily_limit: int) -> "Destination":
        candidate = self._validated(self.id, internal_name, self.telegram_chat_id,
                                    destination_type, access_level, self.status, daily_limit,
                                    self.created_at, self.updated_at)
        return replace(candidate, telegram_chat_id=self.telegram_chat_id)

    def approve_transition(self, target: DestinationStatus) -> DestinationStatus:
        if not isinstance(target, DestinationStatus) or target is self.status:
            raise InvalidDestinationStatusTransitionError("destination status transition is invalid")
        return target


@dataclass(frozen=True, slots=True)
class DestinationExchangePriority:
    exchanges: tuple[Exchange, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.exchanges, tuple) or not 1 <= len(self.exchanges) <= 3:
            raise InvalidExchangePriorityError("priority must contain one to three exchanges")
        if any(not isinstance(item, Exchange) for item in self.exchanges):
            raise InvalidExchangePriorityError("priority contains an invalid exchange")
        if len(set(self.exchanges)) != len(self.exchanges):
            raise InvalidExchangePriorityError("priority contains duplicate exchanges")

    def enable(self, exchange: Exchange) -> "DestinationExchangePriority":
        if exchange in self.exchanges: raise InvalidExchangePriorityError("exchange is already enabled")
        return DestinationExchangePriority(self.exchanges + (exchange,))

    def disable(self, exchange: Exchange) -> "DestinationExchangePriority":
        return DestinationExchangePriority(tuple(item for item in self.exchanges if item is not exchange))

    def move(self, exchange: Exchange, direction: int) -> "DestinationExchangePriority":
        if exchange not in self.exchanges or direction not in {-1, 1}: raise InvalidExchangePriorityError("priority move is invalid")
        items = list(self.exchanges); old = items.index(exchange); new = old + direction
        if new < 0 or new >= len(items): return self
        items[old], items[new] = items[new], items[old]
        return DestinationExchangePriority(tuple(items))


@dataclass(frozen=True, slots=True)
class DestinationSignalistAssignment:
    destination_id: int
    signalist_id: int
    created_at: datetime

    def __post_init__(self) -> None:
        _integer(self.destination_id, "destination_id")
        _integer(self.signalist_id, "signalist_id")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
