from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


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
