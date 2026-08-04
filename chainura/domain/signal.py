from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import re

from chainura.domain.destination import Exchange
from chainura.errors import InvalidSignalInputError

ENTRY_TOLERANCE_PERCENT = Decimal("0.05")
_PRICE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_LEVERAGE = re.compile(r"^[0-9]+$")


class SignalMode(str, Enum):
    COMPOSITE = "composite"
    SINGLE_EXCHANGE = "single_exchange"


class TradeDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


class SignalLifecycleStatus(str, Enum):
    ACTIVE = "active"


class PriceSourceType(str, Enum):
    COMPOSITE = "composite"
    OURBIT_ONLY = "ourbit_only"
    TOOBIT_ONLY = "toobit_only"
    LBANK_ONLY = "lbank_only"


def parse_price(value: object) -> Decimal:
    if not isinstance(value, str) or not _PRICE.fullmatch(value.strip()):
        raise InvalidSignalInputError("price must be a positive plain ASCII decimal")
    result = Decimal(value.strip())
    if not result.is_finite() or result <= 0:
        raise InvalidSignalInputError("price must be positive and finite")
    return result


def parse_leverage(value: object) -> int:
    if not isinstance(value, str) or not _LEVERAGE.fullmatch(value.strip()):
        raise InvalidSignalInputError("leverage must be a positive ASCII integer")
    result = int(value.strip())
    if result < 1:
        raise InvalidSignalInputError("leverage must be at least one")
    return result


def _positive_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise InvalidSignalInputError(f"{name} must be a positive finite Decimal")
    return value


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidSignalInputError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def entry_difference_percent(entry: Decimal, reference: Decimal) -> Decimal:
    entry = _positive_decimal(entry, "entry_price")
    reference = _positive_decimal(reference, "reference_price")
    return abs(entry - reference) / reference * Decimal("100")


def entry_is_within_tolerance(entry: Decimal, reference: Decimal) -> bool:
    return entry_difference_percent(entry, reference) <= ENTRY_TOLERANCE_PERCENT


@dataclass(frozen=True, slots=True)
class Signal:
    id: int | None
    signalist_id: int
    creation_token: str
    mode: SignalMode
    canonical_symbol: str
    base_asset: str
    quote_asset: str
    selected_exchange: Exchange | None
    direction: TradeDirection
    entry_price: Decimal
    leverage: int
    take_profits: tuple[Decimal, ...]
    stop_loss: Decimal
    reference_price: Decimal
    price_source_type: PriceSourceType
    status: SignalLifecycleStatus
    exchange_symbols: tuple[tuple[Exchange, str], ...]
    created_at: datetime
    updated_at: datetime
    initial_source_prices: tuple[tuple[Exchange, Decimal], ...]

    def __post_init__(self) -> None:
        if self.id is not None and (isinstance(self.id, bool) or not isinstance(self.id, int) or self.id < 1):
            raise InvalidSignalInputError("id must be positive")
        if isinstance(self.signalist_id, bool) or not isinstance(self.signalist_id, int) or self.signalist_id < 1:
            raise InvalidSignalInputError("signalist_id must be positive")
        if not isinstance(self.creation_token, str) or not self.creation_token.strip():
            raise InvalidSignalInputError("creation_token is required")
        for name in ("canonical_symbol", "base_asset", "quote_asset"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or not value.isascii():
                raise InvalidSignalInputError(f"{name} must be non-empty ASCII")
        if not isinstance(self.mode, SignalMode) or not isinstance(self.direction, TradeDirection):
            raise InvalidSignalInputError("signal enum is invalid")
        if not isinstance(self.price_source_type, PriceSourceType) or self.status is not SignalLifecycleStatus.ACTIVE:
            raise InvalidSignalInputError("signal state is invalid")
        entry = _positive_decimal(self.entry_price, "entry_price")
        _positive_decimal(self.stop_loss, "stop_loss")
        _positive_decimal(self.reference_price, "reference_price")
        if isinstance(self.leverage, bool) or not isinstance(self.leverage, int) or self.leverage < 1:
            raise InvalidSignalInputError("leverage must be a positive integer")
        if not isinstance(self.take_profits, tuple) or not self.take_profits:
            raise InvalidSignalInputError("at least one take profit is required")
        if any(not isinstance(x, Decimal) or not x.is_finite() or x <= 0 for x in self.take_profits):
            raise InvalidSignalInputError("take profits must be positive finite Decimals")
        if len(set(self.take_profits)) != len(self.take_profits):
            raise InvalidSignalInputError("duplicate take profits are invalid")
        if self.direction is TradeDirection.LONG:
            valid_tp = all(x > entry for x in self.take_profits) and all(a < b for a, b in zip(self.take_profits, self.take_profits[1:]))
            valid_sl = self.stop_loss < entry
        else:
            valid_tp = all(x < entry for x in self.take_profits) and all(a > b for a, b in zip(self.take_profits, self.take_profits[1:]))
            valid_sl = self.stop_loss > entry
        if not valid_tp: raise InvalidSignalInputError("take-profit direction or order is invalid")
        if not valid_sl: raise InvalidSignalInputError("stop-loss direction is invalid")
        if not isinstance(self.exchange_symbols, tuple) or not self.exchange_symbols:
            raise InvalidSignalInputError("exchange symbol mappings are required")
        exchanges = [x[0] for x in self.exchange_symbols]
        if len(set(exchanges)) != len(exchanges) or any(not isinstance(e, Exchange) or not isinstance(s, str) or not s for e, s in self.exchange_symbols):
            raise InvalidSignalInputError("exchange symbol mappings are invalid")
        snapshot_exchanges = [item[0] for item in self.initial_source_prices]
        if (len(set(snapshot_exchanges)) != len(snapshot_exchanges)
                or any(not isinstance(exchange, Exchange) or not isinstance(price, Decimal)
                       or not price.is_finite() or price <= 0
                       for exchange, price in self.initial_source_prices)
                or not set(snapshot_exchanges).issubset(set(exchanges))):
            raise InvalidSignalInputError("initial source-price snapshot is invalid")
        if self.mode is SignalMode.COMPOSITE and len(snapshot_exchanges) < 2:
            raise InvalidSignalInputError("composite snapshot requires at least two sources")
        if self.mode is SignalMode.SINGLE_EXCHANGE and snapshot_exchanges != [self.selected_exchange]:
            raise InvalidSignalInputError("single-exchange snapshot is inconsistent")
        if self.mode is SignalMode.COMPOSITE:
            if self.selected_exchange is not None or self.price_source_type is not PriceSourceType.COMPOSITE:
                raise InvalidSignalInputError("composite signal invariants are invalid")
        else:
            expected = {Exchange.OURBIT: PriceSourceType.OURBIT_ONLY, Exchange.TOOBIT: PriceSourceType.TOOBIT_ONLY, Exchange.LBANK: PriceSourceType.LBANK_ONLY}
            if self.selected_exchange not in expected or self.price_source_type is not expected[self.selected_exchange] or exchanges != [self.selected_exchange]:
                raise InvalidSignalInputError("single-exchange signal invariants are invalid")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
