from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from types import MappingProxyType

from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType, TradeDirection
from chainura.errors import InvalidSignalInputError

class SignalRenderEventKind(str, Enum):
    INITIAL = "initial"
    TP = "tp"
    SL = "sl"
    BREAKEVEN = "breakeven"
class TimeDisplayStrategy(str, Enum):
    ELAPSED = "elapsed"
    TEHRAN_WALL_CLOCK = "tehran_wall_clock"
    UTC_WALL_CLOCK = "utc_wall_clock"

TIME_DISPLAY_STRATEGIES = MappingProxyType({
    Exchange.OURBIT: TimeDisplayStrategy.ELAPSED,
    Exchange.TOOBIT: TimeDisplayStrategy.TEHRAN_WALL_CLOCK,
    Exchange.LBANK: TimeDisplayStrategy.TEHRAN_WALL_CLOCK,
    Exchange.WEEX: TimeDisplayStrategy.TEHRAN_WALL_CLOCK,
})

@dataclass(frozen=True, slots=True)
class ImageSpec:
    width: int
    height: int
    format: str
    content_type: str

EXCHANGE_IMAGE_SPECS = MappingProxyType({
    Exchange.OURBIT: ImageSpec(width=1080, height=1148, format="PNG", content_type="image/png"),
    Exchange.TOOBIT: ImageSpec(width=942, height=1010, format="PNG", content_type="image/png"),
    Exchange.LBANK: ImageSpec(width=830, height=1005, format="PNG", content_type="image/png"),
    Exchange.WEEX: ImageSpec(width=875, height=969,format="PNG", content_type="image/png"),
})

def _positive_decimal(value: object, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise InvalidSignalInputError(f"{name} must be a positive finite Decimal")

@dataclass(frozen=True, slots=True)
class SignalRenderPayload:
    signal_id: int
    event_kind: SignalRenderEventKind
    exchange: Exchange
    direction: TradeDirection
    price_source_type: PriceSourceType
    canonical_symbol: str
    native_symbol: str
    base_asset: str
    quote_asset: str
    entry_price: Decimal
    leverage: int
    take_profits: tuple[Decimal, ...]
    stop_loss: Decimal | None
    reference_price: Decimal
    display_price: Decimal
    initial_pnl_percent: Decimal
    signal_created_at: datetime
    rendered_at: datetime
    elapsed_seconds: int
    coin_logo_path: Path | None = None

    def __post_init__(self) -> None:
        if isinstance(self.signal_id, bool) or not isinstance(self.signal_id, int) or self.signal_id < 1:
            raise InvalidSignalInputError("signal_id must be positive")
        if (not isinstance(self.event_kind, SignalRenderEventKind) or not isinstance(self.exchange, Exchange)
                or not isinstance(self.direction, TradeDirection) or not isinstance(self.price_source_type, PriceSourceType)):
            raise InvalidSignalInputError("render enums are invalid")
        for name in ("canonical_symbol", "native_symbol", "base_asset", "quote_asset"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or not value.isascii():
                raise InvalidSignalInputError(f"{name} must be non-empty ASCII")
        for name in ("entry_price", "reference_price", "display_price"):
            _positive_decimal(getattr(self, name), name)
        if isinstance(self.leverage, bool) or not isinstance(self.leverage, int) or self.leverage < 1:
            raise InvalidSignalInputError("leverage must be a positive integer")
        if (not isinstance(self.take_profits, tuple)
                or any(not isinstance(x, Decimal) or not x.is_finite() or x <= 0 for x in self.take_profits)):
            raise InvalidSignalInputError("take profits must be positive finite Decimals")
        if len(set(self.take_profits)) != len(self.take_profits):
            raise InvalidSignalInputError("take profits must be unique")
        if self.event_kind is SignalRenderEventKind.INITIAL and not self.take_profits and self.stop_loss is None:
            ordered = valid_sl = True
        elif self.stop_loss is None or not self.take_profits:
            ordered = valid_sl = False
        elif self.direction is TradeDirection.LONG:
            ordered = all(value > self.entry_price for value in self.take_profits) and all(
                first < second for first,second in zip(self.take_profits,self.take_profits[1:]))
            valid_sl = self.stop_loss < self.entry_price
        else:
            ordered = all(value < self.entry_price for value in self.take_profits) and all(
                first > second for first,second in zip(self.take_profits,self.take_profits[1:]))
            valid_sl = self.stop_loss > self.entry_price
        if not ordered or not valid_sl:
            raise InvalidSignalInputError("render trade prices violate direction")
        if not isinstance(self.initial_pnl_percent, Decimal) or not self.initial_pnl_percent.is_finite():
            raise InvalidSignalInputError("initial PnL must be a finite Decimal")
        if isinstance(self.elapsed_seconds, bool) or not isinstance(self.elapsed_seconds, int) or self.elapsed_seconds < 0:
            raise InvalidSignalInputError("elapsed duration must be a nonnegative integer")
        for name in ("signal_created_at", "rendered_at"):
            value = getattr(self, name)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise InvalidSignalInputError(f"{name} must be aware")
        if self.rendered_at < self.signal_created_at:
            raise InvalidSignalInputError("rendered event time cannot precede signal creation")
        if self.event_kind is SignalRenderEventKind.INITIAL and self.elapsed_seconds != 0:
            raise InvalidSignalInputError("initial elapsed duration must be zero")
        if self.coin_logo_path is not None and not isinstance(self.coin_logo_path, Path):
            raise InvalidSignalInputError("coin_logo_path must be Path or None")

@dataclass(frozen=True, slots=True)
class RenderedSignalImage:
    file_path: Path
    width: int
    height: int
    content_type: str
    sha256: str
