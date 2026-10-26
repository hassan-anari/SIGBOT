from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKeyConstraint, Index, Integer, Text, UniqueConstraint, Date
from sqlalchemy.orm import Mapped, mapped_column

from chainura.domain.signalist import SignalistStatus
from chainura.domain.destination import (DestinationAccessLevel, DestinationStatus,
                                          DestinationType, Exchange)
from chainura.persistence.base import Base
from chainura.persistence.types import UTCDateTime, ExactDecimal
from chainura.domain.signal import (PriceSourceType, SignalEntryType,
                                    SignalLifecycleStatus, SignalMode, TradeDirection)
from chainura.domain.publication import PublicationRequestStatus, PublicationStatus
from chainura.domain.monitoring import (MonitorStage, SignalEventKind, ResultKind,
    CandidateStatus, PromptStatus, NotificationKind, NotificationStatus)
from chainura.domain.message_template import DestinationMessageKind
from chainura.domain.position import PositionActionKind

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
                                                      default=DestinationStatus.ACTIVE)
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

DESTINATION_MESSAGE_KIND_ENUM = _enum(DestinationMessageKind, "destination_message_kind")

class DestinationMessageTemplateRecord(Base):
    __tablename__ = "destination_message_templates"
    __table_args__ = (
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                             name="fk_destination_message_templates_destination_id_destinations"),
        CheckConstraint("trim(template_text) <> ''", name="ck_destination_message_templates_text_nonempty"),
        Index("uq_destination_message_templates_global_kind", "message_kind", unique=True,
              sqlite_where=__import__('sqlalchemy').text("destination_id IS NULL")),
        Index("uq_destination_message_templates_destination_kind", "destination_id", "message_kind", unique=True,
              sqlite_where=__import__('sqlalchemy').text("destination_id IS NOT NULL")),
        Index("ix_destination_message_templates_destination", "destination_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_kind: Mapped[DestinationMessageKind] = mapped_column(DESTINATION_MESSAGE_KIND_ENUM, nullable=False)
    destination_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

class WarmupRequestRecord(Base):
    __tablename__ = "warmup_requests"
    __table_args__ = (
        ForeignKeyConstraint(["signalist_id"], ["signalists.id"], name="fk_warmup_requests_signalist"),
        Index("ix_warmup_requests_signalist_created", "signalist_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signalist_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

class DestinationWarmupCooldownRecord(Base):
    __tablename__ = "destination_warmup_cooldowns"
    __table_args__ = (
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE", name="fk_warmup_cooldowns_destination"),
        ForeignKeyConstraint(["last_request_id"], ["warmup_requests.id"], name="fk_warmup_cooldowns_request"),
        CheckConstraint("next_allowed_at > reserved_at", name="ck_warmup_cooldowns_window"),
        Index("ix_warmup_cooldowns_next_allowed", "next_allowed_at"),
    )
    destination_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_request_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    next_allowed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

WARMUP_PUBLICATION_STATUS_ENUM = _enum(PublicationStatus, "warmup_publication_status")

class WarmupPublicationRecord(Base):
    __tablename__ = "warmup_publications"
    __table_args__ = (
        ForeignKeyConstraint(["request_id"], ["warmup_requests.id"], ondelete="CASCADE", name="fk_warmup_publications_request"),
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE", name="fk_warmup_publications_destination"),
        UniqueConstraint("request_id", "destination_id", name="uq_warmup_publications_request_destination"),
        CheckConstraint("attempt_count >= 0", name="ck_warmup_publications_attempts"),
        CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0", name="ck_warmup_publications_message"),
        CheckConstraint("(status = 'sent' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status <> 'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)", name="ck_warmup_publications_sent"),
        Index("ix_warmup_publications_status_retry", "status", "retry_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(WARMUP_PUBLICATION_STATUS_ENUM, nullable=False, default=PublicationStatus.PENDING)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_category: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

SIGNAL_MODE_ENUM = _enum(SignalMode, "signal_mode")
TRADE_DIRECTION_ENUM = _enum(TradeDirection, "trade_direction")
PRICE_SOURCE_ENUM = _enum(PriceSourceType, "signal_price_source_type")
SIGNAL_STATUS_ENUM = _enum(SignalLifecycleStatus, "signal_lifecycle_status")
SIGNAL_ENTRY_TYPE_ENUM = _enum(SignalEntryType, "signal_entry_type")

class SignalRecord(Base):
    __tablename__ = "signals"
    __table_args__ = (
        ForeignKeyConstraint(["signalist_id"], ["signalists.id"], name="fk_signals_signalist_id_signalists"),
        UniqueConstraint("creation_token", name="uq_signals_creation_token"),
        CheckConstraint("leverage >= 1", name="ck_signals_leverage_positive"),
        CheckConstraint("trim(canonical_symbol) <> ''", name="ck_signals_symbol_nonempty"),
        CheckConstraint("(status = 'cancelled' AND cancelled_at IS NOT NULL) OR (status <> 'cancelled' AND cancelled_at IS NULL)",name="ck_signals_cancelled_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signalist_id: Mapped[int] = mapped_column(Integer, nullable=False)
    creation_token: Mapped[str] = mapped_column(Text, nullable=False)
    entry_type: Mapped[SignalEntryType] = mapped_column(
        SIGNAL_ENTRY_TYPE_ENUM, nullable=False, default=SignalEntryType.MARKET)
    mode: Mapped[SignalMode] = mapped_column(SIGNAL_MODE_ENUM, nullable=False)
    canonical_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    base_asset: Mapped[str] = mapped_column(Text, nullable=False)
    quote_asset: Mapped[str] = mapped_column(Text, nullable=False)
    selected_exchange: Mapped[Exchange | None] = mapped_column(DESTINATION_EXCHANGE_ENUM, nullable=True)
    direction: Mapped[TradeDirection] = mapped_column(TRADE_DIRECTION_ENUM, nullable=False)
    entry_price: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_loss: Mapped[object | None] = mapped_column(ExactDecimal(), nullable=True)
    reference_price: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    price_source_type: Mapped[PriceSourceType] = mapped_column(PRICE_SOURCE_ENUM, nullable=False)
    status: Mapped[SignalLifecycleStatus] = mapped_column(SIGNAL_STATUS_ENUM, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
SignalRecord.__table__.primary_key.name = "pk_signals"

POSITION_ACTION_KIND_ENUM = _enum(PositionActionKind, "position_action_kind")

class SignalPositionStateRecord(Base):
    __tablename__ = "signal_position_states"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE", name="fk_signal_position_states_signal"),
        CheckConstraint("CAST(remaining_fraction AS NUMERIC) >= 0 AND CAST(remaining_fraction AS NUMERIC) <= 1", name="ck_signal_position_states_fraction"),
        CheckConstraint("version >= 1", name="ck_signal_position_states_version"),
        CheckConstraint("(is_closed = 1 AND CAST(remaining_fraction AS NUMERIC) = 0 AND closed_at IS NOT NULL) OR (is_closed = 0 AND CAST(remaining_fraction AS NUMERIC) > 0 AND closed_at IS NULL)", name="ck_signal_position_states_closed_shape"),
        CheckConstraint("risk_free_active = 0 OR (risk_free_used = 1 AND effective_stop_price IS NOT NULL AND is_closed = 0)", name="ck_signal_position_states_risk_free"),
        Index("ix_signal_position_states_open", "is_closed", "signal_id"),
    )
    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    remaining_fraction: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    realized_weighted_roi: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    risk_free_used: Mapped[bool] = mapped_column(nullable=False, default=False)
    risk_free_active: Mapped[bool] = mapped_column(nullable=False, default=False)
    save_profit_50_used: Mapped[bool] = mapped_column(nullable=False, default=False)
    effective_stop_price: Mapped[object | None] = mapped_column(ExactDecimal())
    is_closed: Mapped[bool] = mapped_column(nullable=False, default=False)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
SignalPositionStateRecord.__table__.primary_key.name = "pk_signal_position_states"

class SignalPositionActionRecord(Base):
    __tablename__ = "signal_position_actions"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE", name="fk_signal_position_actions_signal"),
        ForeignKeyConstraint(["event_id"], ["signal_events.id"], name="fk_signal_position_actions_event"),
        ForeignKeyConstraint(["candidate_id"], ["signal_update_candidates.id"], name="fk_signal_position_actions_candidate"),
        UniqueConstraint("candidate_id", name="uq_signal_position_actions_candidate"),
        UniqueConstraint("event_id", "action_kind", name="uq_signal_position_actions_event_kind"),
        CheckConstraint("CAST(remaining_fraction_before AS NUMERIC) >= 0 AND CAST(remaining_fraction_before AS NUMERIC) <= 1 AND CAST(closed_fraction AS NUMERIC) >= 0 AND CAST(remaining_fraction_after AS NUMERIC) >= 0 AND CAST(remaining_fraction_after AS NUMERIC) <= CAST(remaining_fraction_before AS NUMERIC) AND CAST(remaining_fraction_before AS NUMERIC) - CAST(closed_fraction AS NUMERIC) = CAST(remaining_fraction_after AS NUMERIC)", name="ck_signal_position_actions_fractions"),
        CheckConstraint("(CAST(closed_fraction AS NUMERIC) = 0 AND CAST(weighted_roi_delta AS NUMERIC) = 0) OR CAST(closed_fraction AS NUMERIC) > 0", name="ck_signal_position_actions_zero_close"),
        Index("ix_signal_position_actions_signal_created", "signal_id", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[int | None] = mapped_column(Integer)
    action_kind: Mapped[PositionActionKind] = mapped_column(POSITION_ACTION_KIND_ENUM, nullable=False)
    remaining_fraction_before: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    closed_fraction: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    remaining_fraction_after: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    execution_price: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    leveraged_roi: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    weighted_roi_delta: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    realized_weighted_roi_after: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
SignalPositionActionRecord.__table__.primary_key.name = "pk_signal_position_actions"

class SignalPositionActionPublicationRecord(Base):
    __tablename__ = "signal_position_action_publications"
    __table_args__ = (
        ForeignKeyConstraint(["action_id"], ["signal_position_actions.id"], ondelete="CASCADE", name="fk_position_action_publications_action"),
        ForeignKeyConstraint(["candidate_id"], ["signal_update_candidates.id"], ondelete="CASCADE", name="fk_position_action_publications_candidate"),
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE", name="fk_position_action_publications_signal"),
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE", name="fk_position_action_publications_destination"),
        ForeignKeyConstraint(["tp_publication_id"], ["destination_update_publications.id"], ondelete="CASCADE", name="fk_position_action_publications_tp_publication"),
        UniqueConstraint("action_id", "destination_id", name="uq_position_action_publications_action_destination"),
        CheckConstraint("(candidate_id IS NULL AND tp_publication_id IS NULL) OR (candidate_id IS NOT NULL AND tp_publication_id IS NOT NULL)", name="ck_position_action_publications_origin"),
        CheckConstraint("attempt_count >= 0", name="ck_position_action_publications_attempts"),
        CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0", name="ck_position_action_publications_message"),
        CheckConstraint("reply_to_message_id IS NULL OR reply_to_message_id > 0", name="ck_position_action_publications_reply"),
        CheckConstraint("(status = 'sent' AND telegram_message_id IS NOT NULL AND reply_to_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status <> 'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)", name="ck_position_action_publications_sent"),
        Index("ix_position_action_publications_status_retry", "status", "retry_at"),
        Index("ix_position_action_publications_chain", "signal_id", "destination_id", "sent_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[int | None] = mapped_column(Integer)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tp_publication_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[PublicationStatus] = mapped_column(_enum(PublicationStatus,"position_action_publication_status"), nullable=False, default=PublicationStatus.PENDING)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_category: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

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


class LimitEntryTriggerRecord(Base):
    __tablename__ = "limit_entry_triggers"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE",
                             name="fk_limit_entry_triggers_signal"),
        CheckConstraint("CAST(requested_entry AS NUMERIC) > 0 AND CAST(observed_reference_price AS NUMERIC) > 0",
                        name="ck_limit_entry_triggers_prices"),
        Index("ix_limit_entry_triggers_occurred", "occurred_at"),
    )
    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requested_entry: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    observed_reference_price: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)
    price_source_type: Mapped[PriceSourceType] = mapped_column(PRICE_SOURCE_ENUM, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class LimitEntryTriggerSourcePriceRecord(Base):
    __tablename__ = "limit_entry_trigger_source_prices"
    __table_args__ = (ForeignKeyConstraint(["signal_id"], ["limit_entry_triggers.signal_id"],
                                           ondelete="CASCADE", name="fk_limit_trigger_prices_trigger"),)
    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exchange: Mapped[Exchange] = mapped_column(DESTINATION_EXCHANGE_ENUM, primary_key=True)
    price: Mapped[object] = mapped_column(ExactDecimal(), nullable=False)


class LimitPendingPublicationRecord(Base):
    __tablename__ = "limit_pending_publications"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE"),
        UniqueConstraint("signal_id", "destination_id", name="uq_limit_pending_signal_destination"),
        CheckConstraint("attempt_count >= 0", name="ck_limit_pending_attempts"),
        CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0", name="ck_limit_pending_message"),
        CheckConstraint("(status = 'sent' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR "
                        "(status <> 'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)",
                        name="ck_limit_pending_sent"),
        Index("ix_limit_pending_status_retry", "status", "retry_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_id: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_exchange: Mapped[Exchange] = mapped_column(DESTINATION_EXCHANGE_ENUM, nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(_enum(PublicationStatus, "limit_pending_publication_status"), nullable=False,
                                                       default=PublicationStatus.PENDING)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_category: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)


class LimitAdmissionRequestRecord(Base):
    __tablename__ = "limit_admission_requests"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE"),
        UniqueConstraint("signal_id", name="uq_limit_admission_request_signal"),
        Index("ix_limit_admission_requests_status", "status", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    planned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class LimitCancellationPublicationRecord(Base):
    __tablename__ = "limit_cancellation_publications"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["pending_publication_id"], ["limit_pending_publications.id"], ondelete="CASCADE"),
        UniqueConstraint("signal_id", "destination_id", name="uq_limit_cancellation_signal_destination"),
        CheckConstraint("reply_to_message_id > 0", name="ck_limit_cancellation_reply"),
        CheckConstraint("attempt_count >= 0", name="ck_limit_cancellation_attempts"),
        CheckConstraint("(status = 'sent' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR "
                        "(status <> 'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)",
                        name="ck_limit_cancellation_sent"),
        Index("ix_limit_cancellation_status_retry", "status", "retry_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_id: Mapped[int] = mapped_column(Integer, nullable=False)
    pending_publication_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reply_to_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(
        _enum(PublicationStatus, "limit_cancellation_publication_status"), nullable=False,
        default=PublicationStatus.PENDING)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_category: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

class SignalTemplateRecord(Base):
    __tablename__="signal_templates"
    __table_args__=(
      ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE",name="fk_signal_templates_destination_id_destinations"),
      CheckConstraint("trim(storage_key) <> '' AND substr(storage_key,1,1) <> '/' AND instr(storage_key,'..') = 0",name="ck_signal_templates_storage_key_safe"),
      CheckConstraint("width > 0 AND height > 0",name="ck_signal_templates_dimensions_positive"),
      CheckConstraint("(exchange='ourbit' AND width=1080 AND height=1148) OR (exchange='toobit' AND width=942 AND height=1010) OR (exchange='lbank' AND width=830 AND height=1005) OR (exchange='weex' AND width=875 AND height=969)",name="ck_signal_templates_exchange_dimensions"),
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

PUBLICATION_REQUEST_STATUS_ENUM = _enum(PublicationRequestStatus, "initial_publication_request_status")
PUBLICATION_STATUS_ENUM = _enum(PublicationStatus, "destination_publication_status")

class InitialPublicationRequestRecord(Base):
    __tablename__ = "initial_publication_requests"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE", name="fk_initial_publication_requests_signal_id_signals"),
        UniqueConstraint("signal_id", name="uq_initial_publication_requests_signal_id"),
        Index("ix_initial_publication_requests_status_created", "status", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PublicationRequestStatus] = mapped_column(PUBLICATION_REQUEST_STATUS_ENUM, nullable=False, default=PublicationRequestStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    planned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
InitialPublicationRequestRecord.__table__.primary_key.name = "pk_initial_publication_requests"

class InitialPublicationNotificationRecord(Base):
    __tablename__="initial_publication_notifications"
    __table_args__=(ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),
        UniqueConstraint("signal_id",name="uq_initial_publication_notification_signal"),
        Index("ix_initial_publication_notification_status","status","created_at"))
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    signal_id:Mapped[int]=mapped_column(Integer,nullable=False)
    status:Mapped[str]=mapped_column(Text,nullable=False,default="pending")
    outcome:Mapped[str]=mapped_column(Text,nullable=False)
    telegram_message_id:Mapped[int|None]=mapped_column(BigInteger)
    created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)
    updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)
    sent_at:Mapped[datetime|None]=mapped_column(UTCDateTime())

class DestinationPublicationRecord(Base):
    __tablename__ = "destination_publications"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE", name="fk_destination_publications_signal_id_signals"),
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE", name="fk_destination_publications_destination_id_destinations"),
        UniqueConstraint("signal_id", "destination_id", "event_kind", name="uq_destination_publications_signal_destination_event"),
        CheckConstraint("event_kind = 'initial'", name="ck_destination_publications_event_initial"),
        CheckConstraint("selected_exchange IN ('ourbit','toobit','lbank','weex')", name="ck_destination_publications_exchange"),
        CheckConstraint("attempt_count >= 0", name="ck_destination_publications_attempt_nonnegative"),
        CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0", name="ck_destination_publications_message_positive"),
        CheckConstraint("(origin = 'market' AND reply_to_message_id IS NULL) OR "
                        "(origin = 'limit_activation' AND reply_to_message_id IS NOT NULL)",
                        name="ck_destination_publications_origin_reply"),
        CheckConstraint("(status = 'sent' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status <> 'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)", name="ck_destination_publications_sent_consistency"),
        Index("ix_destination_publications_status_updated", "status", "updated_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_kind: Mapped[str] = mapped_column(Text, nullable=False, default="initial")
    origin: Mapped[str] = mapped_column(Text, nullable=False, default="market")
    selected_exchange: Mapped[Exchange] = mapped_column(DESTINATION_EXCHANGE_ENUM, nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(PUBLICATION_STATUS_ENUM, nullable=False, default=PublicationStatus.PENDING)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_category: Mapped[str | None] = mapped_column(Text)
    template_sha256: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    first_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
DestinationPublicationRecord.__table__.primary_key.name = "pk_destination_publications"

class DestinationTradeLockRecord(Base):
    """The durable owner of one destination/canonical-symbol publication slot."""
    __tablename__ = "destination_trade_locks"
    __table_args__ = (
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE",
                             name="fk_destination_trade_locks_destination"),
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE",
                             name="fk_destination_trade_locks_signal"),
        CheckConstraint("trim(canonical_symbol) <> ''",
                        name="ck_destination_trade_locks_symbol_nonempty"),
        Index("ix_destination_trade_locks_signal_id", "signal_id"),
    )
    destination_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_symbol: Mapped[str] = mapped_column(Text, primary_key=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

DestinationTradeLockRecord.__table__.primary_key.name = "pk_destination_trade_locks"

class SignalDetailsPublicationRecord(Base):
    """Durable destination-specific Stage-Two text outbox."""
    __tablename__ = "signal_details_publications"
    __table_args__ = (
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE"),
        UniqueConstraint("signal_id", "destination_id", name="uq_signal_details_destination"),
        CheckConstraint("attempt_count >= 0", name="ck_signal_details_attempts"),
        CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0", name="ck_signal_details_message"),
        CheckConstraint("reply_to_message_id IS NULL OR reply_to_message_id > 0", name="ck_signal_details_reply"),
        Index("ix_signal_details_status_retry", "status", "retry_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(PUBLICATION_STATUS_ENUM, nullable=False, default=PublicationStatus.PENDING)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_category: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)

class SignalCancellationPublicationRecord(Base):
    __tablename__="signal_cancellation_publications"
    __table_args__=(
      ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),
      ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE"),
      UniqueConstraint("signal_id","destination_id",name="uq_signal_cancellation_destination"),
      CheckConstraint("attempt_count >= 0",name="ck_signal_cancellation_attempts"),
      CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0",name="ck_signal_cancellation_message"),
      CheckConstraint("reply_to_message_id IS NULL OR reply_to_message_id > 0",name="ck_signal_cancellation_reply"),
      Index("ix_signal_cancellation_status_retry","status","retry_at"),)
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    signal_id:Mapped[int]=mapped_column(Integer,nullable=False);destination_id:Mapped[int]=mapped_column(Integer,nullable=False)
    status:Mapped[PublicationStatus]=mapped_column(PUBLICATION_STATUS_ENUM,nullable=False,default=PublicationStatus.PENDING)
    telegram_message_id:Mapped[int|None]=mapped_column(BigInteger);reply_to_message_id:Mapped[int|None]=mapped_column(BigInteger)
    attempt_count:Mapped[int]=mapped_column(Integer,nullable=False,default=0);retry_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    first_attempt_at:Mapped[datetime|None]=mapped_column(UTCDateTime());sent_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    last_error_category:Mapped[str|None]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)

class SignalCommentRequestRecord(Base):
    """Signalist-authored plain-text comment and its optional captured market facts."""
    __tablename__="signal_comment_requests"
    __table_args__=(
      ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),
      ForeignKeyConstraint(["signalist_id"],["signalists.id"]),
      CheckConstraint("trim(comment_text) <> '' AND length(comment_text) <= 4096",name="ck_signal_comment_text"),
      CheckConstraint("(include_snapshot = 0 AND snapshot_observed_at IS NULL AND reference_price IS NULL AND source_prices_json IS NULL) OR (include_snapshot = 1 AND snapshot_observed_at IS NOT NULL AND reference_price IS NOT NULL AND source_prices_json IS NOT NULL)",name="ck_signal_comment_snapshot_shape"),
      Index("ix_signal_comment_requests_signal_created","signal_id","created_at"),)
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True)
    signal_id:Mapped[int]=mapped_column(Integer,nullable=False);signalist_id:Mapped[int]=mapped_column(Integer,nullable=False)
    comment_text:Mapped[str]=mapped_column(Text,nullable=False);include_snapshot:Mapped[bool]=mapped_column(nullable=False,default=False)
    snapshot_observed_at:Mapped[datetime|None]=mapped_column(UTCDateTime());reference_price:Mapped[object|None]=mapped_column(ExactDecimal())
    source_prices_json:Mapped[str|None]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)

class SignalCommentPublicationRecord(Base):
    __tablename__="signal_comment_publications"
    __table_args__=(
      ForeignKeyConstraint(["comment_request_id"],["signal_comment_requests.id"],ondelete="CASCADE"),
      ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE"),ForeignKeyConstraint(["destination_id"],["destinations.id"],ondelete="CASCADE"),
      UniqueConstraint("comment_request_id","destination_id",name="uq_signal_comment_destination"),
      CheckConstraint("attempt_count >= 0",name="ck_signal_comment_publication_attempts"),
      CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0",name="ck_signal_comment_publication_message"),
      CheckConstraint("reply_to_message_id IS NULL OR reply_to_message_id > 0",name="ck_signal_comment_publication_reply"),
      Index("ix_signal_comment_publications_status_retry","status","retry_at"),Index("ix_signal_comment_publications_chain","signal_id","destination_id","sent_at"),)
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True);comment_request_id:Mapped[int]=mapped_column(Integer,nullable=False)
    signal_id:Mapped[int]=mapped_column(Integer,nullable=False);destination_id:Mapped[int]=mapped_column(Integer,nullable=False)
    selected_exchange:Mapped[Exchange|None]=mapped_column(DESTINATION_EXCHANGE_ENUM)
    status:Mapped[PublicationStatus]=mapped_column(PUBLICATION_STATUS_ENUM,nullable=False,default=PublicationStatus.PENDING)
    telegram_message_id:Mapped[int|None]=mapped_column(BigInteger);reply_to_message_id:Mapped[int|None]=mapped_column(BigInteger)
    attempt_count:Mapped[int]=mapped_column(Integer,nullable=False,default=0);retry_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    first_attempt_at:Mapped[datetime|None]=mapped_column(UTCDateTime());sent_at:Mapped[datetime|None]=mapped_column(UTCDateTime());last_error_category:Mapped[str|None]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)

class InitialPublicationDailySlotRecord(Base):
    __tablename__ = "initial_publication_daily_slots"
    __table_args__ = (
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE", name="fk_initial_publication_daily_slots_destination_id_destinations"),
        ForeignKeyConstraint(["publication_id"], ["destination_publications.id"], ondelete="CASCADE", name="fk_initial_publication_daily_slots_publication_id_publications"),
        ForeignKeyConstraint(["limit_publication_id"], ["limit_pending_publications.id"], ondelete="CASCADE", name="fk_initial_publication_daily_slots_limit_publication"),
        UniqueConstraint("destination_id", "local_date", "slot_number", name="uq_initial_publication_daily_slots_destination_date_slot"),
        UniqueConstraint("publication_id", name="uq_initial_publication_daily_slots_publication_id"),
        UniqueConstraint("limit_publication_id", name="uq_initial_publication_daily_slots_limit_publication"),
        CheckConstraint("(publication_id IS NOT NULL) <> (limit_publication_id IS NOT NULL)",
                        name="ck_initial_publication_daily_slots_one_owner"),
        CheckConstraint("slot_number > 0", name="ck_initial_publication_daily_slots_slot_positive"),
        Index("ix_initial_publication_daily_slots_destination_date", "destination_id", "local_date"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    destination_id: Mapped[int] = mapped_column(Integer, nullable=False)
    local_date: Mapped[object] = mapped_column(Date, nullable=False)
    slot_number: Mapped[int] = mapped_column(Integer, nullable=False)
    publication_id: Mapped[int | None] = mapped_column(Integer)
    limit_publication_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
InitialPublicationDailySlotRecord.__table__.primary_key.name = "pk_initial_publication_daily_slots"

MONITOR_STAGE_ENUM=_enum(MonitorStage,"signal_monitor_stage")
EVENT_KIND_ENUM=_enum(SignalEventKind,"signal_event_kind")
RESULT_KIND_ENUM=_enum(ResultKind,"signal_result_kind")
CANDIDATE_STATUS_ENUM=_enum(CandidateStatus,"signal_update_candidate_status")
PROMPT_STATUS_ENUM=_enum(PromptStatus,"breakeven_prompt_status")
NOTIFICATION_KIND_ENUM=_enum(NotificationKind,"signalist_notification_kind")
NOTIFICATION_STATUS_ENUM=_enum(NotificationStatus,"signalist_notification_status")

class SignalMonitorStateRecord(Base):
    __tablename__="signal_monitor_states"
    __table_args__=(ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_monitor_states_signal_id_signals"),
      CheckConstraint("next_tp_index > 0 AND highest_tp_index >= 0 AND breakeven_cycle >= 0 AND version > 0",name="ck_signal_monitor_states_counters"),
      CheckConstraint("stage <> 'pre_tp1' OR highest_tp_index = 0",name="ck_signal_monitor_states_pre_tp1"),
      CheckConstraint("stage NOT IN ('completed_sl','completed_breakeven','completed_targets') OR completed_at IS NOT NULL",name="ck_signal_monitor_states_completion"),
      CheckConstraint("stage <> 'completed_breakeven' OR current_leveraged_roi = '0'",name="ck_signal_monitor_states_breakeven_zero"),
      Index("ix_signal_monitor_states_stage_checked","stage","last_checked_at"))
    signal_id:Mapped[int]=mapped_column(Integer,primary_key=True)
    stage:Mapped[MonitorStage]=mapped_column(MONITOR_STAGE_ENUM,nullable=False,default=MonitorStage.PRE_TP1)
    next_tp_index:Mapped[int]=mapped_column(Integer,nullable=False,default=1);highest_tp_index:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    favorable_threshold_reached_at:Mapped[datetime|None]=mapped_column(UTCDateTime());breakeven_armed_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    breakeven_cycle:Mapped[int]=mapped_column(Integer,nullable=False,default=0);currently_inside_breakeven_zone:Mapped[bool]=mapped_column(nullable=False,default=False)
    breakeven_rearm_reached_at:Mapped[datetime|None]=mapped_column(UTCDateTime());last_breakeven_prompt_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    last_checked_at:Mapped[datetime|None]=mapped_column(UTCDateTime());last_reference_price:Mapped[object|None]=mapped_column(ExactDecimal())
    current_result_kind:Mapped[ResultKind|None]=mapped_column(RESULT_KIND_ENUM);current_result_price:Mapped[object|None]=mapped_column(ExactDecimal())
    current_leveraged_roi:Mapped[object|None]=mapped_column(ExactDecimal());completed_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    disabled_reason:Mapped[str|None]=mapped_column(Text);version:Mapped[int]=mapped_column(Integer,nullable=False,default=1)
    created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)

class SignalEventRecord(Base):
    __tablename__="signal_events"
    __table_args__=(ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_events_signal_id_signals"),
      UniqueConstraint("signal_id","tp_index",name="uq_signal_events_signal_tp"),
      CheckConstraint("(kind='tp_hit' AND tp_index > 0 AND threshold_price IS NOT NULL) OR (kind<>'tp_hit' AND tp_index IS NULL)",name="ck_signal_events_tp_shape"),
      Index("uq_signal_events_one_manual_close","signal_id",unique=True,sqlite_where=__import__('sqlalchemy').text("kind='manual_close'")),
      Index("uq_signal_events_one_sl","signal_id",unique=True,sqlite_where=__import__('sqlalchemy').text("kind='sl_hit'")),
      Index("uq_signal_events_one_breakeven","signal_id",unique=True,sqlite_where=__import__('sqlalchemy').text("kind='breakeven'")),Index("ix_signal_events_signal_occurred","signal_id","occurred_at"))
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True);signal_id:Mapped[int]=mapped_column(Integer,nullable=False)
    kind:Mapped[SignalEventKind]=mapped_column(EVENT_KIND_ENUM,nullable=False);tp_index:Mapped[int|None]=mapped_column(Integer)
    threshold_price:Mapped[object|None]=mapped_column(ExactDecimal());observed_reference_price:Mapped[object]=mapped_column(ExactDecimal(),nullable=False)
    roi_basis_price:Mapped[object]=mapped_column(ExactDecimal(),nullable=False);leveraged_roi:Mapped[object]=mapped_column(ExactDecimal(),nullable=False)
    price_source_type:Mapped[PriceSourceType]=mapped_column(PRICE_SOURCE_ENUM,nullable=False);occurred_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False)
    created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)

class SignalEventSourcePriceRecord(Base):
    __tablename__="signal_event_source_prices"
    __table_args__=(ForeignKeyConstraint(["event_id"],["signal_events.id"],ondelete="CASCADE",name="fk_signal_event_source_prices_event_id_signal_events"),)
    event_id:Mapped[int]=mapped_column(Integer,primary_key=True);exchange:Mapped[Exchange]=mapped_column(DESTINATION_EXCHANGE_ENUM,primary_key=True);price:Mapped[object]=mapped_column(ExactDecimal(),nullable=False)

class SignalUpdateCandidateRecord(Base):
    __tablename__="signal_update_candidates"
    __table_args__=(ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_signal_update_candidates_signal_id_signals"),ForeignKeyConstraint(["event_id"],["signal_events.id"],ondelete="CASCADE",name="fk_signal_update_candidates_event_id_signal_events"),ForeignKeyConstraint(["signalist_id"],["signalists.id"],name="fk_signal_update_candidates_signalist_id_signalists"),ForeignKeyConstraint(["superseded_by_event_id"],["signal_events.id"],name="fk_signal_update_candidates_superseded_event"),UniqueConstraint("event_id",name="uq_signal_update_candidates_event_id"),CheckConstraint("NOT (approved_at IS NOT NULL AND declined_at IS NOT NULL)",name="ck_signal_update_candidates_decision"),Index("ix_signal_update_candidates_status_created","status","created_at"))
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True);signal_id:Mapped[int]=mapped_column(Integer,nullable=False);event_id:Mapped[int]=mapped_column(Integer,nullable=False);signalist_id:Mapped[int]=mapped_column(Integer,nullable=False)
    status:Mapped[CandidateStatus]=mapped_column(CANDIDATE_STATUS_ENUM,nullable=False);superseded_by_event_id:Mapped[int|None]=mapped_column(Integer);approved_at:Mapped[datetime|None]=mapped_column(UTCDateTime());declined_at:Mapped[datetime|None]=mapped_column(UTCDateTime());created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)


class UpdatePublicationRequestRecord(Base):
    __tablename__ = "update_publication_requests"
    __table_args__ = (
        ForeignKeyConstraint(["candidate_id"], ["signal_update_candidates.id"], ondelete="CASCADE", name="fk_update_publication_requests_candidate_id_candidates"),
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE", name="fk_update_publication_requests_signal_id_signals"),
        ForeignKeyConstraint(["event_id"], ["signal_events.id"], ondelete="CASCADE", name="fk_update_publication_requests_event_id_events"),
        UniqueConstraint("candidate_id", name="uq_update_publication_requests_candidate_id"),
        UniqueConstraint("event_id", name="uq_update_publication_requests_event_id"),
        Index("ix_update_publication_requests_status_created", "status", "created_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PublicationRequestStatus] = mapped_column(PUBLICATION_REQUEST_STATUS_ENUM, nullable=False, default=PublicationRequestStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    planned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
UpdatePublicationRequestRecord.__table__.primary_key.name = "pk_update_publication_requests"

class DestinationUpdatePublicationRecord(Base):
    __tablename__ = "destination_update_publications"
    __table_args__ = (
        ForeignKeyConstraint(["request_id"], ["update_publication_requests.id"], ondelete="CASCADE", name="fk_destination_update_publications_request_id_requests"),
        ForeignKeyConstraint(["candidate_id"], ["signal_update_candidates.id"], ondelete="CASCADE", name="fk_destination_update_publications_candidate_id_candidates"),
        ForeignKeyConstraint(["signal_id"], ["signals.id"], ondelete="CASCADE", name="fk_destination_update_publications_signal_id_signals"),
        ForeignKeyConstraint(["event_id"], ["signal_events.id"], ondelete="CASCADE", name="fk_destination_update_publications_event_id_events"),
        ForeignKeyConstraint(["destination_id"], ["destinations.id"], ondelete="CASCADE", name="fk_destination_update_publications_destination_id_destinations"),
        ForeignKeyConstraint(["event_id","selected_exchange"], ["signal_event_source_prices.event_id","signal_event_source_prices.exchange"], ondelete="CASCADE", name="fk_destination_update_publications_event_source_price"),
        UniqueConstraint("candidate_id", "destination_id", name="uq_destination_update_publications_candidate_destination"),
        UniqueConstraint("event_id", "destination_id", name="uq_destination_update_publications_event_destination"),
        CheckConstraint("selected_exchange IN ('ourbit','toobit','lbank','weex')", name="ck_destination_update_publications_exchange"),
        CheckConstraint("attempt_count >= 0", name="ck_destination_update_publications_attempt_nonnegative"),
        CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0", name="ck_destination_update_publications_message_positive"),
        CheckConstraint("reply_to_message_id IS NULL OR reply_to_message_id > 0", name="ck_destination_update_publications_reply_positive"),
        CheckConstraint("(status = 'sent' AND telegram_message_id IS NOT NULL AND reply_to_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status <> 'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)", name="ck_destination_update_publications_sent_consistency"),
        Index("ix_destination_update_publications_status_retry", "status", "retry_at"),
        Index("ix_destination_update_publications_chain", "signal_id", "destination_id", "sent_at"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    destination_id: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_exchange: Mapped[Exchange] = mapped_column(DESTINATION_EXCHANGE_ENUM, nullable=False)
    event_kind: Mapped[SignalEventKind] = mapped_column(EVENT_KIND_ENUM, nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(PUBLICATION_STATUS_ENUM, nullable=False, default=PublicationStatus.PENDING)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error_category: Mapped[str | None] = mapped_column(Text)
    template_sha256: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utc_now)
DestinationUpdatePublicationRecord.__table__.primary_key.name = "pk_destination_update_publications"

class BreakevenPromptRecord(Base):
    __tablename__="breakeven_prompts"
    __table_args__=(ForeignKeyConstraint(["signal_id"],["signals.id"],ondelete="CASCADE",name="fk_breakeven_prompts_signal_id_signals"),UniqueConstraint("signal_id","cycle_number",name="uq_breakeven_prompts_signal_cycle"),CheckConstraint("cycle_number > 0",name="ck_breakeven_prompts_cycle_positive"),Index("uq_breakeven_prompts_one_open","signal_id",unique=True,sqlite_where=__import__('sqlalchemy').text("status IN ('pending_notification','awaiting_response')")))
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True);signal_id:Mapped[int]=mapped_column(Integer,nullable=False);cycle_number:Mapped[int]=mapped_column(Integer,nullable=False);status:Mapped[PromptStatus]=mapped_column(PROMPT_STATUS_ENUM,nullable=False);observed_price_at_creation:Mapped[object]=mapped_column(ExactDecimal(),nullable=False);telegram_message_id:Mapped[int|None]=mapped_column(BigInteger);created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);notified_at:Mapped[datetime|None]=mapped_column(UTCDateTime());declined_at:Mapped[datetime|None]=mapped_column(UTCDateTime());accepted_at:Mapped[datetime|None]=mapped_column(UTCDateTime());expired_at:Mapped[datetime|None]=mapped_column(UTCDateTime())

class SignalistNotificationRecord(Base):
    __tablename__="signalist_notifications"
    __table_args__=(ForeignKeyConstraint(["candidate_id"],["signal_update_candidates.id"],ondelete="CASCADE",name="fk_signalist_notifications_candidate_id_candidates"),ForeignKeyConstraint(["prompt_id"],["breakeven_prompts.id"],ondelete="CASCADE",name="fk_signalist_notifications_prompt_id_prompts"),ForeignKeyConstraint(["limit_trigger_signal_id"],["limit_entry_triggers.signal_id"],ondelete="CASCADE",name="fk_signalist_notifications_limit_trigger"),ForeignKeyConstraint(["signalist_id"],["signalists.id"],name="fk_signalist_notifications_signalist_id_signalists"),UniqueConstraint("candidate_id",name="uq_signalist_notifications_candidate_id"),UniqueConstraint("prompt_id",name="uq_signalist_notifications_prompt_id"),UniqueConstraint("limit_trigger_signal_id",name="uq_signalist_notifications_limit_trigger"),CheckConstraint("(kind='event' AND candidate_id IS NOT NULL AND prompt_id IS NULL AND limit_trigger_signal_id IS NULL) OR (kind='breakeven_prompt' AND prompt_id IS NOT NULL AND candidate_id IS NULL AND limit_trigger_signal_id IS NULL) OR (kind='limit_trigger' AND limit_trigger_signal_id IS NOT NULL AND candidate_id IS NULL AND prompt_id IS NULL)",name="ck_signalist_notifications_source"),CheckConstraint("attempt_count >= 0",name="ck_signalist_notifications_attempts"),Index("ix_signalist_notifications_status_retry","status","retry_at"))
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True);kind:Mapped[NotificationKind]=mapped_column(NOTIFICATION_KIND_ENUM,nullable=False);candidate_id:Mapped[int|None]=mapped_column(Integer);prompt_id:Mapped[int|None]=mapped_column(Integer);limit_trigger_signal_id:Mapped[int|None]=mapped_column(Integer);signalist_id:Mapped[int]=mapped_column(Integer,nullable=False);status:Mapped[NotificationStatus]=mapped_column(NOTIFICATION_STATUS_ENUM,nullable=False,default=NotificationStatus.PENDING);interaction_version:Mapped[int]=mapped_column(Integer,nullable=False,default=2);attempt_count:Mapped[int]=mapped_column(Integer,nullable=False,default=0);retry_at:Mapped[datetime|None]=mapped_column(UTCDateTime());telegram_message_id:Mapped[int|None]=mapped_column(BigInteger);last_error_category:Mapped[str|None]=mapped_column(Text);created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);sent_at:Mapped[datetime|None]=mapped_column(UTCDateTime())

from chainura.domain.health import ApiOperation,ServiceHealthStatus,AdminAlertKind,AdminAlertStatus
API_OPERATION_ENUM=_enum(ApiOperation,"api_health_operation")
SERVICE_HEALTH_STATUS_ENUM=_enum(ServiceHealthStatus,"service_health_status")
ADMIN_ALERT_KIND_ENUM=_enum(AdminAlertKind,"admin_alert_kind")
ADMIN_ALERT_STATUS_ENUM=_enum(AdminAlertStatus,"admin_alert_status")

class ServiceHealthRecord(Base):
    __tablename__="service_health_states"
    __table_args__=(CheckConstraint("service_key IN ('ourbit_prices','ourbit_catalog','toobit_prices','toobit_catalog','lbank_prices','lbank_catalog','weex_prices','weex_catalog')",name="ck_service_health_key"),CheckConstraint("consecutive_successes >= 0 AND consecutive_failures >= 0",name="ck_service_health_counters"),ForeignKeyConstraint(["current_incident_id"],["outage_incidents.id"],name="fk_service_health_states_current_incident_id_incidents"),CheckConstraint("(status='down' AND current_incident_id IS NOT NULL AND unhealthy_since IS NOT NULL) OR (status<>'down' AND current_incident_id IS NULL)",name="ck_service_health_current_incident"),Index("ix_service_health_status_repeat","status","next_repeat_at"))
    service_key:Mapped[str]=mapped_column(Text,primary_key=True)
    exchange:Mapped[Exchange]=mapped_column(DESTINATION_EXCHANGE_ENUM,nullable=False)
    operation:Mapped[ApiOperation]=mapped_column(API_OPERATION_ENUM,nullable=False)
    status:Mapped[ServiceHealthStatus]=mapped_column(SERVICE_HEALTH_STATUS_ENUM,nullable=False,default=ServiceHealthStatus.UNKNOWN)
    last_attempt_at:Mapped[datetime|None]=mapped_column(UTCDateTime());last_success_at:Mapped[datetime|None]=mapped_column(UTCDateTime());last_failure_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    unhealthy_since:Mapped[datetime|None]=mapped_column(UTCDateTime());next_repeat_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    consecutive_successes:Mapped[int]=mapped_column(Integer,nullable=False,default=0);consecutive_failures:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    last_error_category:Mapped[str|None]=mapped_column(Text);current_incident_id:Mapped[int|None]=mapped_column(Integer)
    created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)

class OutageIncidentRecord(Base):
    __tablename__="outage_incidents"
    __table_args__=(CheckConstraint("service_key IN ('ourbit_prices','ourbit_catalog','toobit_prices','toobit_catalog','lbank_prices','lbank_catalog','weex_prices','weex_catalog')",name="ck_outage_incidents_key"),Index("uq_outage_incidents_one_open","service_key",unique=True,sqlite_where=__import__('sqlalchemy').text("recovered_at IS NULL")),Index("ix_outage_incidents_service_started","service_key","started_at"))
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True);service_key:Mapped[str]=mapped_column(Text,nullable=False)
    exchange:Mapped[Exchange]=mapped_column(DESTINATION_EXCHANGE_ENUM,nullable=False);operation:Mapped[ApiOperation]=mapped_column(API_OPERATION_ENUM,nullable=False)
    started_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False);recovered_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    initial_error_category:Mapped[str|None]=mapped_column(Text);latest_error_category:Mapped[str|None]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)
OutageIncidentRecord.__table__.primary_key.name="pk_outage_incidents"

class AdminAlertRecord(Base):
    __tablename__="admin_alert_outbox"
    __table_args__=(ForeignKeyConstraint(["incident_id"],["outage_incidents.id"],ondelete="CASCADE",name="fk_admin_alert_outbox_incident_id_incidents"),CheckConstraint("attempts >= 0",name="ck_admin_alert_outbox_attempts"),CheckConstraint("telegram_message_id IS NULL OR telegram_message_id > 0",name="ck_admin_alert_outbox_message_positive"),CheckConstraint("(status='sent' AND telegram_message_id IS NOT NULL AND sent_at IS NOT NULL) OR (status<>'sent' AND telegram_message_id IS NULL AND sent_at IS NULL)",name="ck_admin_alert_outbox_sent_consistency"),UniqueConstraint("incident_id","kind","repeat_sequence",name="uq_admin_alert_outbox_logical"),Index("ix_admin_alert_outbox_status_retry","status","retry_at"))
    id:Mapped[int]=mapped_column(Integer,primary_key=True,autoincrement=True);incident_id:Mapped[int]=mapped_column(Integer,nullable=False);service_key:Mapped[str]=mapped_column(Text,nullable=False)
    kind:Mapped[AdminAlertKind]=mapped_column(ADMIN_ALERT_KIND_ENUM,nullable=False);repeat_sequence:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    status:Mapped[AdminAlertStatus]=mapped_column(ADMIN_ALERT_STATUS_ENUM,nullable=False,default=AdminAlertStatus.PENDING);attempts:Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    retry_at:Mapped[datetime|None]=mapped_column(UTCDateTime());telegram_message_id:Mapped[int|None]=mapped_column(BigInteger);sent_at:Mapped[datetime|None]=mapped_column(UTCDateTime())
    sanitized_error:Mapped[str|None]=mapped_column(Text);created_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now);updated_at:Mapped[datetime]=mapped_column(UTCDateTime(),nullable=False,default=utc_now)
AdminAlertRecord.__table__.primary_key.name="pk_admin_alert_outbox"
