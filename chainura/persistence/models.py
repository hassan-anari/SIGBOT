from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from chainura.domain.signalist import SignalistStatus
from chainura.domain.destination import (DestinationAccessLevel, DestinationStatus,
                                          DestinationType, Exchange)
from chainura.persistence.base import Base
from chainura.persistence.types import UTCDateTime, ExactDecimal
from chainura.domain.signal import (PriceSourceType, SignalLifecycleStatus, SignalMode,
                                    TradeDirection)

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

SIGNAL_MODE_ENUM = _enum(SignalMode, "signal_mode")
TRADE_DIRECTION_ENUM = _enum(TradeDirection, "trade_direction")
PRICE_SOURCE_ENUM = _enum(PriceSourceType, "signal_price_source_type")
SIGNAL_STATUS_ENUM = _enum(SignalLifecycleStatus, "signal_lifecycle_status")

class SignalRecord(Base):
    __tablename__ = "signals"
    __table_args__ = (
        ForeignKeyConstraint(["signalist_id"], ["signalists.id"], name="fk_signals_signalist_id_signalists"),
        UniqueConstraint("creation_token", name="uq_signals_creation_token"),
        CheckConstraint("leverage >= 1", name="ck_signals_leverage_positive"),
        CheckConstraint("trim(canonical_symbol) <> ''", name="ck_signals_symbol_nonempty"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signalist_id: Mapped[int] = mapped_column(Integer, nullable=False)
    creation_token: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[SignalMode] = mapped_column(SIGNAL_MODE_ENUM, nullable=False)
    canonical_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    base_asset: Mapped[str] = mapped_column(Text, nullable=False)
    quote_asset: Mapped[str] = mapped_column(Text, nullable=False)
    selected_exchange: Mapped[Exchange | None] = mapped_column(DESTINATION_EXCHANGE_ENUM, nullable=True)
    direction: Mapped[TradeDirection] = mapped_column(TRADE_DIRECTION_ENUM, nullable=False)
    entry_price: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_loss: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    reference_price: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    price_source_type: Mapped[PriceSourceType] = mapped_column(PRICE_SOURCE_ENUM, nullable=False)
    status: Mapped[SignalLifecycleStatus] = mapped_column(SIGNAL_STATUS_ENUM, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
SignalRecord.__table__.primary_key.name = "pk_signals"

class SignalTakeProfitRecord(Base):
    __tablename__ = "signal_take_profits"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE", name="fk_signal_take_profits_signal_id_signals"),
        UniqueConstraint("signal_id", "position", name="uq_signal_take_profits_signal_position"),
        CheckConstraint("position >= 1", name="ck_signal_take_profits_position_positive"),
    )
    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    price: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)

class SignalExchangeSymbolRecord(Base):
    __tablename__ = "signal_exchange_symbols"
    __table_args__ = (ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE", name="fk_signal_exchange_symbols_signal_id_signals"),)
    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[Exchange] = mapped_column(DESTINATION_EXCHANGE_ENUM, primary_key=True)
    native_symbol: Mapped[str] = mapped_column(Text, nullable=False)

class SignalInitialPriceRecord(Base):
    __tablename__ = "signal_initial_prices"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE",
                             name="fk_signal_initial_prices_signal_id_signals"),
        ForeignKeyConstraint(["signal_id","exchange"],
            ["signal_exchange_symbols.signal_id","signal_exchange_symbols.exchange"],
            ondelete="CASCADE",name="fk_signal_initial_prices_signal_exchange_symbols"),
        CheckConstraint("trim(price) <> ''", name="ck_signal_initial_prices_price_nonempty"),
        CheckConstraint("CAST(price AS NUMERIC) > 0", name="ck_signal_initial_prices_price_positive"),
    )
    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[Exchange] = mapped_column(DESTINATION_EXCHANGE_ENUM, primary_key=True)
    price: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)

class SignalTemplateRecord(Base):
    __tablename__="signal_templates"
    __table_args__=(
      ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE",name="fk_signal_templates_destination_id_destinations"),
      CheckConstraint("trim(storage_key) <> '' AND substr(storage_key,1,1) <> '/' AND instr(storage_key,'..') = 0",name="ck_signal_templates_storage_key_safe"),
      CheckConstraint("width > 0 AND height > 0",name="ck_signal_templates_dimensions_positive"),
      CheckConstraint("(exchange='ourbit' AND width=1080 AND height=1148) OR (exchange='toobit' AND width=942 AND height=1010) OR (exchange='lbank' AND width=830 AND height=1005)",name="ck_signal_templates_exchange_dimensions"),
      CheckConstraint("content_type = 'image/png'",name="ck_signal_templates_content_type_png"),
      Index("uq_signal_templates_default_exchange","exchange",unique=True,sqlite_where=__import__('sqlalchemy').text("destination_id IS NULL")),
      Index("uq_signal_templates_destination_exchange","destination_id","exchange",unique=True,sqlite_where=__import__('sqlalchemy').text("destination_id IS NOT NULL")),)
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    exchange:Mapped[Exchange]=mapped_column(DESTINATION_EXCHANGE_ENUM,nullable=False)
    destination_id:Mapped[int|None]=mapped_column(Integer,nullable=True)
    storage_key:Mapped[str]=mapped_column(Text,nullable=False)
    sha256:Mapped[str]=mapped_column(Text,nullable=False)
    width:Mapped[int]=mapped_column(Integer,nullable=False); height:Mapped[int]=mapped_column(Integer,nullable=False)
    content_type:Mapped[str]=mapped_column(Text,nullable=False)
    created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)
    updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)
