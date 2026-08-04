from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from chainura.domain.destination import Exchange


class PublicationRequestStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    PLANNED = "planned"
    COMPLETED = "completed"
    FAILED = "failed"


class PublicationStatus(str, Enum):
    PENDING = "pending"
    RENDERING = "rendering"
    READY = "ready"
    SENDING = "sending"
    RETRY_WAIT = "retry_wait"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED_INACTIVE = "skipped_inactive"
    SKIPPED_UNASSIGNED = "skipped_unassigned"
    SKIPPED_DAILY_LIMIT = "skipped_daily_limit"
    DELIVERY_UNKNOWN = "delivery_unknown"
    BLOCKED_TEMPLATE = "blocked_template"


TERMINAL_PUBLICATION_STATUSES = frozenset({
    PublicationStatus.SENT, PublicationStatus.FAILED,
    PublicationStatus.SKIPPED_INACTIVE, PublicationStatus.SKIPPED_UNASSIGNED,
    PublicationStatus.SKIPPED_DAILY_LIMIT, PublicationStatus.DELIVERY_UNKNOWN,
})


def select_initial_exchange(mode, selected_exchange, priorities, source_exchanges):
    """Select solely from the immutable validation snapshot and frozen priority order."""
    available = frozenset(source_exchanges)
    if getattr(mode, "value", mode) == "single_exchange":
        return selected_exchange if selected_exchange in priorities and selected_exchange in available else None
    return next((exchange for exchange in priorities if exchange in available), None)


def tehran_local_date(now: datetime):
    from zoneinfo import ZoneInfo
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock must return an aware datetime")
    return now.astimezone(ZoneInfo("Asia/Tehran")).date()


@dataclass(frozen=True, slots=True)
class PublicationDelivery:
    id: int
    signal_id: int
    destination_id: int
    selected_exchange: Exchange
    status: PublicationStatus
    attempt_count: int
    telegram_chat_id: int
    private: bool
    daily_limit: int
