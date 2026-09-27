"""Pure Decimal position accounting for the Phase 4A persistence foundation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum


ZERO = Decimal("0")
ONE = Decimal("1")


class PositionActionKind(str, Enum):
    NORMAL_TP = "normal_tp"
    RISK_FREE = "risk_free"
    SAVE_PROFIT_50 = "save_profit_50"
    FULL_CLOSE = "full_close"
    FINAL_TP_CLOSE = "final_tp_close"
    STOP_LOSS_CLOSE = "stop_loss_close"
    RISK_FREE_HIT_CLOSE = "risk_free_hit_close"


class InvalidPositionTransition(ValueError):
    """The requested mutation is incompatible with the current position."""


@dataclass(frozen=True, slots=True)
class PositionState:
    remaining_fraction: Decimal
    realized_weighted_roi: Decimal
    risk_free_used: bool
    risk_free_active: bool
    save_profit_50_used: bool
    effective_stop_price: Decimal | None
    is_closed: bool = False
    closed_at: datetime | None = None

    def __post_init__(self) -> None:
        decimals = (self.remaining_fraction, self.realized_weighted_roi)
        if any(not isinstance(value, Decimal) or not value.is_finite() for value in decimals):
            raise InvalidPositionTransition("position values must be finite Decimals")
        if not ZERO <= self.remaining_fraction <= ONE:
            raise InvalidPositionTransition("remaining fraction must be between zero and one")
        valid_open = (not self.is_closed and self.remaining_fraction > ZERO
                      and self.closed_at is None)
        valid_closed = (self.is_closed and self.remaining_fraction == ZERO
                        and self.closed_at is not None)
        if not (valid_open or valid_closed):
            raise InvalidPositionTransition("open/closed position shape is inconsistent")
        if self.risk_free_active and (not self.risk_free_used or self.effective_stop_price is None or self.is_closed):
            raise InvalidPositionTransition("active Risk Free state is inconsistent")


@dataclass(frozen=True, slots=True)
class PositionTransition:
    state: PositionState
    action_kind: PositionActionKind
    remaining_fraction_before: Decimal
    closed_fraction: Decimal
    leveraged_roi: Decimal
    weighted_roi_delta: Decimal


def initial_position(stop_loss: Decimal) -> PositionState:
    _decimal(stop_loss, "stop price")
    return PositionState(ONE, ZERO, False, False, False, stop_loss)


def apply_position_action(state: PositionState, action: PositionActionKind, *,
                          leveraged_roi: Decimal = ZERO,
                          entry_price: Decimal | None = None,
                          occurred_at: datetime | None = None) -> PositionTransition:
    if state.is_closed:
        raise InvalidPositionTransition("a closed position cannot be mutated")
    _decimal(leveraged_roi, "leveraged ROI")
    before = state.remaining_fraction
    closed = ZERO
    next_state = state
    if action is PositionActionKind.NORMAL_TP:
        pass
    elif action is PositionActionKind.SAVE_PROFIT_50:
        if state.save_profit_50_used:
            raise InvalidPositionTransition("Save50 may only be used once")
        closed = before / Decimal("2")
        next_state = replace(state, remaining_fraction=before - closed,
                             save_profit_50_used=True)
    elif action is PositionActionKind.RISK_FREE:
        if state.risk_free_used:
            raise InvalidPositionTransition("Risk Free may only be used once")
        _decimal(entry_price, "entry price")
        next_state = replace(state, risk_free_used=True, risk_free_active=True,
                             effective_stop_price=entry_price)
    elif action is PositionActionKind.STOP_LOSS_CLOSE:
        if state.risk_free_active:
            raise InvalidPositionTransition("normal SL is disabled while Risk Free is active")
        closed = before
    elif action is PositionActionKind.RISK_FREE_HIT_CLOSE:
        if not state.risk_free_active:
            raise InvalidPositionTransition("Risk Free is not active")
        if leveraged_roi != ZERO:
            raise InvalidPositionTransition("Risk Free hit ROI must be zero")
        closed = before
    elif action in (PositionActionKind.FULL_CLOSE, PositionActionKind.FINAL_TP_CLOSE):
        closed = before
    else:
        raise InvalidPositionTransition("unsupported position action")
    delta = closed * leveraged_roi
    realized = state.realized_weighted_roi + delta
    if closed == before and closed != ZERO:
        if occurred_at is None or occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise InvalidPositionTransition("closing actions require an aware timestamp")
        next_state = replace(next_state, remaining_fraction=ZERO,
                             realized_weighted_roi=realized,
                             risk_free_active=False, is_closed=True,
                             closed_at=occurred_at)
    elif closed:
        next_state = replace(next_state, realized_weighted_roi=realized)
    return PositionTransition(next_state, action, before, closed, leveraged_roi, delta)


def _decimal(value: object, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InvalidPositionTransition(f"{label} must be a finite Decimal")
