"""Phase 6A monitoring value objects and deterministic trade rules."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType, TradeDirection


class MonitorStage(str, Enum):
    PRE_TP1 = "pre_tp1"
    POST_TP1 = "post_tp1"
    COMPLETED_SL = "completed_sl"
    COMPLETED_BREAKEVEN = "completed_breakeven"
    COMPLETED_TARGETS = "completed_targets"
    DISABLED_LEGACY = "disabled_legacy"


class SignalEventKind(str, Enum):
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    BREAKEVEN = "breakeven"


class ResultKind(str, Enum):
    WIN = "win"
    LOSS = "loss"
    NEUTRAL = "neutral"


class CandidateStatus(str, Enum):
    AWAITING_NOTIFICATION = "awaiting_notification"
    AWAITING_DECISION = "awaiting_decision"
    APPROVED = "approved"
    DECLINED = "declined"
    SUPERSEDED = "superseded"
    INELIGIBLE_INACTIVE = "ineligible_inactive"


class PromptStatus(str, Enum):
    PENDING_NOTIFICATION = "pending_notification"
    AWAITING_RESPONSE = "awaiting_response"
    DECLINED = "declined"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class NotificationKind(str, Enum):
    EVENT = "event"
    BREAKEVEN_PROMPT = "breakeven_prompt"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENDING = "sending"
    RETRY_WAIT = "retry_wait"
    SENT = "sent"
    FAILED = "failed"
    DELIVERY_UNKNOWN = "delivery_unknown"
    SKIPPED_INACTIVE = "skipped_inactive"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class MarketDecision:
    reference_price: Decimal
    source_prices: tuple[tuple[Exchange, Decimal], ...]
    price_source_type: PriceSourceType
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class EventDecision:
    kind: SignalEventKind
    tp_index: int | None
    threshold_price: Decimal
    observed_reference_price: Decimal
    roi_basis_price: Decimal
    leveraged_roi: Decimal


def leveraged_roi(direction: TradeDirection, entry: Decimal, basis: Decimal,
                  leverage: int) -> Decimal:
    """Exact, unrounded ROI; callers persist Decimal text without float conversion."""
    movement = basis - entry if direction is TradeDirection.LONG else entry - basis
    return movement / entry * Decimal("100") * Decimal(leverage)


def reached(direction: TradeDirection, current: Decimal, threshold: Decimal,
            *, target: bool) -> bool:
    if direction is TradeDirection.LONG:
        return current >= threshold if target else current <= threshold
    return current <= threshold if target else current >= threshold


def evaluate_price(*, direction: TradeDirection, entry: Decimal, leverage: int,
                   take_profits: tuple[Decimal, ...], stop_loss: Decimal,
                   next_tp_index: int, stage: MonitorStage,
                   market: MarketDecision) -> tuple[EventDecision, ...]:
    """Return sequential newly traversed events for one coherent market snapshot."""
    if stage not in (MonitorStage.PRE_TP1, MonitorStage.POST_TP1):
        return ()
    events: list[EventDecision] = []
    index = next_tp_index
    while index <= len(take_profits) and reached(
            direction, market.reference_price, take_profits[index - 1], target=True):
        threshold = take_profits[index - 1]
        events.append(EventDecision(SignalEventKind.TP_HIT, index, threshold,
                                    market.reference_price, threshold,
                                    leveraged_roi(direction, entry, threshold, leverage)))
        index += 1
    if events:
        return tuple(events)
    if stage is MonitorStage.PRE_TP1 and reached(
            direction, market.reference_price, stop_loss, target=False):
        return (EventDecision(SignalEventKind.SL_HIT, None, stop_loss,
                              market.reference_price, stop_loss,
                              leveraged_roi(direction, entry, stop_loss, leverage)),)
    return ()


def favorable_percent(direction: TradeDirection, entry: Decimal,
                       current: Decimal) -> Decimal:
    movement = current - entry if direction is TradeDirection.LONG else entry - current
    return movement / entry * Decimal("100")


def entry_distance_percent(entry: Decimal, current: Decimal) -> Decimal:
    return abs(current - entry) / entry * Decimal("100")
