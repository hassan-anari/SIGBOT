from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chainura.domain.signalist import SignalistStatus
from chainura.domain.destination import (DestinationAccessLevel, DestinationStatus,
                                          DestinationType, Exchange)
from chainura.persistence.base import Base
from chainura.persistence.types import UTCDateTime

SIGNALIST_STATUS_ENUM = Enum(
    SignalistStatus,
    name="signalist_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
    values_callable=lambda enum: [member.value for member in enum],
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SignalistRecord(Base):
    __tablename__ = "signalists"
    __table_args__ = (
        CheckConstraint("telegram_user_id > 0", name="ck_signalists_telegram_user_id_positive"),
        UniqueConstraint("telegram_user_id", name="uq_signalists_telegram_user_id"),
        CheckConstraint("trim(display_name) <> ''", name="ck_signalists_display_name_nonempty"),
        CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status <> 'archived' AND archived_at IS NULL)",
            name="ck_signalists_archive_consistency",
        ),
        Index("ix_signalists_status", "status"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True,
    )
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SignalistStatus] = mapped_column(
        SIGNALIST_STATUS_ENUM, nullable=False, default=SignalistStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now,
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


# Explicit names are part of the persistence contract.
SignalistRecord.__table__.primary_key.name = "pk_signalists"

def _enum(enum, name):
    return Enum(enum, name=name, native_enum=False, create_constraint=True,
                validate_strings=True, values_callable=lambda cls: [x.value for x in cls])

DESTINATION_STATUS_ENUM = _enum(DestinationStatus, "destination_status")
DESTINATION_TYPE_ENUM = _enum(DestinationType, "destination_type")
DESTINATION_ACCESS_ENUM = _enum(DestinationAccessLevel, "destination_access_level")
DESTINATION_EXCHANGE_ENUM = _enum(Exchange, "destination_exchange")


class DestinationRecord(Base):
    __tablename__ = "destinations"
    __table_args__ = (
        UniqueConstraint("internal_name", name="uq_destinations_internal_name"),
        UniqueConstraint("telegram_chat_id", name="uq_destinations_telegram_chat_id"),
        CheckConstraint("trim(internal_name) <> ''", name="ck_destinations_internal_name_nonempty"),
        CheckConstraint("telegram_chat_id <> 0", name="ck_destinations_telegram_chat_id_nonzero"),
        CheckConstraint("daily_limit > 0", name="ck_destinations_daily_limit_positive"),
        Index("ix_destinations_status_id", "status", "id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    internal_name: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    destination_type: Mapped[DestinationType] = mapped_column(DESTINATION_TYPE_ENUM, nullable=False)
    access_level: Mapped[DestinationAccessLevel] = mapped_column(DESTINATION_ACCESS_ENUM, nullable=False)
    status: Mapped[DestinationStatus] = mapped_column(DESTINATION_STATUS_ENUM, nullable=False,
                                                      default=DestinationStatus.INACTIVE)
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

DestinationRecord.__table__.primary_key.name = "pk_destinations"


class DestinationExchangePriorityRecord(Base):
    __tablename__ = "destination_exchange_priorities"
    __table_args__ = (
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                             name="fk_destination_exchange_priorities_destination_id_destinations"),
        UniqueConstraint("destination_id", "position",
                         name="uq_destination_exchange_priorities_destination_position"),
        CheckConstraint("position >= 1", name="ck_destination_exchange_priorities_position_positive"),
        Index("ix_destination_exchange_priorities_destination_position", "destination_id", "position"),
    )
    destination_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[Exchange] = mapped_column(DESTINATION_EXCHANGE_ENUM, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

DestinationExchangePriorityRecord.__table__.primary_key.name = "pk_destination_exchange_priorities"


class DestinationSignalistRecord(Base):
    __tablename__ = "destination_signalists"
    __table_args__ = (
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                             name="fk_destination_signalists_destination_id_destinations"),
        ForeignKeyConstraint(["signalist_id"], ["signalists.id"],
                             name="fk_destination_signalists_signalist_id_signalists"),
        Index("ix_destination_signalists_signalist_id_destination_id", "signalist_id", "destination_id"),
    )
    destination_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signalist_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

DestinationSignalistRecord.__table__.primary_key.name = "pk_destination_signalists"
