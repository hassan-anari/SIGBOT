from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from http_client import get_json
from logging_config import get_logger
from models import Instrument, PriceTick

LOGGER = get_logger("exchanges.toobit")
from .common import canonical, decimal_value, extract_rows, first, upper

TickHandler = Callable[[PriceTick], Awaitable[None]]


class ToobitAdapter:
    name = "toobit"
    base_url = "https://api.toobit.com"

    def __init__(self, *, poll_seconds: float = 5.0, timeout: float = 8.0) -> None:
        self.poll_seconds = poll_seconds
        self.timeout = timeout
        self.symbol_map: dict[str, str] = {}

    @staticmethod
    def parse_instruments(payload: Any) -> list[Instrument]:
        contracts = payload.get("contracts", []) if isinstance(payload, dict) else []
        instruments: list[Instrument] = []
        seen: set[str] = set()

        for row in contracts:
            if not isinstance(row, dict):
                continue

            status = upper(row.get("status"))
            inverse = bool(row.get("inverse", False))
            quote = upper(first(row, ("quoteAsset", "marginToken")))
            margin = upper(row.get("marginToken"))
            base = upper(first(row, ("underlying", "baseAsset")))
            exchange_symbol = upper(row.get("symbol"))

            if (
                status != "TRADING"
                or inverse
                or quote != "USDT"
                or margin != "USDT"
                or not base
                or not exchange_symbol
                or exchange_symbol in seen
            ):
                continue

            seen.add(exchange_symbol)
            instruments.append(
                Instrument(
                    exchange="toobit",
                    exchange_symbol=exchange_symbol,
                    canonical_symbol=canonical(base, "USDT"),
                    base_asset=base,
                    quote_asset="USDT",
                )
            )

        return instruments

    @staticmethod
    def parse_prices(payload: Any, symbol_map: dict[str, str]) -> list[PriceTick]:
        ticks: list[PriceTick] = []

        for row in extract_rows(payload):
            exchange_symbol = upper(row.get("s"))
            price = decimal_value(row.get("p"))
            canonical_symbol = symbol_map.get(exchange_symbol)

            if not canonical_symbol or price is None or price <= 0:
                continue

            ticks.append(
                PriceTick.now(
                    exchange="toobit",
                    exchange_symbol=exchange_symbol,
                    canonical_symbol=canonical_symbol,
                    last_price=price,
                    source="rest",
                )
            )

        return ticks

    async def fetch_instruments(self) -> list[Instrument]:
        payload = await get_json(
            f"{self.base_url}/api/v1/exchangeInfo",
            timeout=self.timeout,
        )
        instruments = self.parse_instruments(payload)
        if not instruments:
            raise RuntimeError("Toobit response contained no usable USDT perpetual symbols")

        self.symbol_map = {
            item.exchange_symbol: item.canonical_symbol for item in instruments
        }
        return instruments

    async def fetch_prices(self) -> list[PriceTick]:
        payload = await get_json(
            f"{self.base_url}/quote/v1/contract/ticker/price",
            timeout=self.timeout,
        )
        ticks = self.parse_prices(payload, self.symbol_map)
        if not ticks:
            raise RuntimeError("Toobit response contained no usable last prices")
        return ticks

    async def run(
        self,
        allowed: set[str],
        handler: TickHandler,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            started = asyncio.get_running_loop().time()
            try:
                for tick in await self.fetch_prices():
                    if tick.canonical_symbol in allowed:
                        await handler(tick)
            except Exception as exc:
                print(f"[toobit] error: {exc}")
                LOGGER.exception("polling error")

            elapsed = asyncio.get_running_loop().time() - started
            delay = max(0.0, self.poll_seconds - elapsed)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
