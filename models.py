from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum


@dataclass(frozen=True, slots=True)
class Instrument:
    exchange: str
    exchange_symbol: str
    canonical_symbol: str
    base_asset: str
    quote_asset: str = "USDT"


@dataclass(frozen=True, slots=True)
class PriceTick:
    exchange: str
    exchange_symbol: str
    canonical_symbol: str
    last_price: Decimal
    received_at: datetime
    source: str

    @classmethod
    def now(
        cls,
        *,
        exchange: str,
        exchange_symbol: str,
        canonical_symbol: str,
        last_price: Decimal,
        source: str,
    ) -> "PriceTick":
        return cls(
            exchange=exchange,
            exchange_symbol=exchange_symbol,
            canonical_symbol=canonical_symbol,
            last_price=last_price,
            received_at=datetime.now(timezone.utc),
            source=source,
        )


class ExchangeStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    exchange: str
    attempted_at: datetime
    ticks: tuple[PriceTick, ...]
    expected_symbols: frozenset[str]
    received_symbols: frozenset[str]
    missing_symbols: frozenset[str]

    @property
    def is_complete(self) -> bool:
        return not self.missing_symbols


@dataclass(frozen=True, slots=True)
class HealthTransition:
    exchange: str
    previous_status: ExchangeStatus
    current_status: ExchangeStatus
    occurred_at: datetime
    unhealthy_duration: timedelta | None = None


@dataclass(slots=True)
class ExchangeHealth:
    exchange: str
    status: ExchangeStatus = ExchangeStatus.UNKNOWN
    latest_attempt_at: datetime | None = None
    latest_success_at: datetime | None = None
    latest_complete_at: datetime | None = None
    latest_failure_at: datetime | None = None
    expected_count: int = 0
    received_count: int = 0
    missing_count: int = 0
    last_error: str | None = None
    unhealthy_since: datetime | None = None
