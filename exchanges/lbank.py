from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from http_client import get_json
from logging_config import get_logger
from models import Instrument, PriceTick

LOGGER = get_logger("exchanges.lbank")
from .common import canonical, decimal_value, extract_rows, upper

SnapshotHandler = Callable[[str, list[PriceTick], datetime], Awaitable[None]]
ErrorHandler = Callable[[str, Exception, datetime], Awaitable[None]]


class LBankAdapter:
    name = "lbank"
    base_url = "https://lbkperp.lbank.com"
    product_group = "SwapU"

    def __init__(self, *, poll_seconds: float = 5.0, timeout: float = 8.0) -> None:
        self.poll_seconds = poll_seconds
        self.timeout = timeout
        self.symbol_map: dict[str, str] = {}
        self.numeric_only_candidates: list[str] = []
        self.ignored_numeric_symbols: list[str] = []
        self.preserved_numeric_symbols: list[str] = []
        self.raw_instrument_count = 0

    @staticmethod
    def parse_instruments(payload: Any) -> list[Instrument]:
        """Parse all straightforward linear USDT contracts.

        Numeric-only bases are intentionally kept here. Whether they are useful is
        decided later by the cross-exchange discovery layer. This preserves a real
        common symbol such as 4USDT while still allowing LBank-only internal-looking
        numeric contracts to be removed.
        """
        instruments: list[Instrument] = []
        seen: set[str] = set()

        for row in extract_rows(payload):
            exchange_symbol = upper(row.get("symbol"))
            base = upper(row.get("baseCurrency"))
            clear = upper(row.get("clearCurrency"))
            price_currency = upper(row.get("priceCurrency"))

            if (
                not exchange_symbol
                or not base
                or clear != "USDT"
                or price_currency != "USDT"
                or exchange_symbol in seen
            ):
                continue

            # Keep only the straightforward linear USDT naming used by this probe.
            if exchange_symbol != canonical(base, "USDT"):
                continue

            seen.add(exchange_symbol)
            instruments.append(
                Instrument(
                    exchange="lbank",
                    exchange_symbol=exchange_symbol,
                    canonical_symbol=canonical(base, "USDT"),
                    base_asset=base,
                    quote_asset="USDT",
                )
            )

        return instruments

    @staticmethod
    def numeric_only_symbols(payload: Any) -> list[str]:
        candidates: list[str] = []
        for row in extract_rows(payload):
            base = upper(row.get("baseCurrency"))
            exchange_symbol = upper(row.get("symbol"))
            if base and base.isdigit() and exchange_symbol:
                candidates.append(exchange_symbol)
        return sorted(set(candidates))

    @staticmethod
    def parse_prices(payload: Any, symbol_map: dict[str, str]) -> list[PriceTick]:
        ticks: list[PriceTick] = []

        for row in extract_rows(payload):
            exchange_symbol = upper(row.get("symbol"))
            price = decimal_value(row.get("lastPrice"))
            canonical_symbol = symbol_map.get(exchange_symbol)

            if not canonical_symbol or price is None or price <= 0:
                continue

            ticks.append(
                PriceTick.now(
                    exchange="lbank",
                    exchange_symbol=exchange_symbol,
                    canonical_symbol=canonical_symbol,
                    last_price=price,
                    source="rest",
                )
            )

        return ticks

    async def fetch_instruments(self) -> list[Instrument]:
        payload = await get_json(
            f"{self.base_url}/cfd/openApi/v1/pub/instrument",
            params={"productGroup": self.product_group},
            timeout=self.timeout,
        )
        self.numeric_only_candidates = self.numeric_only_symbols(payload)
        instruments = self.parse_instruments(payload)
        self.raw_instrument_count = len(instruments)
        if not instruments:
            raise RuntimeError("LBank response contained no usable USDT perpetual symbols")

        self.symbol_map = {
            item.exchange_symbol: item.canonical_symbol for item in instruments
        }
        return instruments

    async def fetch_prices(self) -> list[PriceTick]:
        payload = await get_json(
            f"{self.base_url}/cfd/openApi/v1/pub/marketData",
            params={"productGroup": self.product_group},
            timeout=self.timeout,
        )
        ticks = self.parse_prices(payload, self.symbol_map)
        if not ticks:
            raise RuntimeError("LBank response contained no usable futures last prices")
        return ticks

    async def run(
        self,
        allowed: set[str],
        snapshot_handler: SnapshotHandler,
        error_handler: ErrorHandler,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            started = asyncio.get_running_loop().time()
            attempted_at = datetime.now(timezone.utc)
            try:
                ticks = await self.fetch_prices()
                await snapshot_handler(self.name, ticks, attempted_at)
            except Exception as exc:
                print(f"[lbank] error: {exc}")
                LOGGER.exception("polling error")
                await error_handler(self.name, exc, attempted_at)

            elapsed = asyncio.get_running_loop().time() - started
            delay = max(0.0, self.poll_seconds - elapsed)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
