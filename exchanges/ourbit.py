from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from http_client import get_json
from logging_config import get_logger
from models import Instrument, PriceTick

LOGGER = get_logger("exchanges.ourbit")
from .common import canonical, decimal_value, extract_rows, first, infer_base_from_symbol, upper

SnapshotHandler = Callable[[str, list[PriceTick], datetime], Awaitable[None]]
ErrorHandler = Callable[[str, Exception, datetime], Awaitable[None]]


class OurbitAdapter:
    name = "ourbit"
    url = "https://futures.ourbit.com/api/v1/contract/ticker"

    def __init__(self, *, poll_seconds: float = 5.0, timeout: float = 8.0) -> None:
        self.poll_seconds = poll_seconds
        self.timeout = timeout
        self.symbol_map: dict[str, str] = {}

    @staticmethod
    def parse_instruments(payload: Any) -> list[Instrument]:
        instruments: list[Instrument] = []
        seen: set[str] = set()

        for row in extract_rows(payload):
            exchange_symbol = upper(row.get("symbol"))
            if not exchange_symbol or exchange_symbol in seen:
                continue

            quote = upper(first(row, ("quoteCoin", "quoteAsset", "quoteCurrency"))) or "USDT"
            base = upper(first(row, ("baseCoin", "baseAsset", "baseCurrency")))
            if not base:
                base = infer_base_from_symbol(exchange_symbol, quote)

            status = upper(first(row, ("status", "state", "tradeStatus")))
            if not base or quote != "USDT" or status in {"OFFLINE", "SUSPENDED", "CLOSED", "0"}:
                continue

            seen.add(exchange_symbol)
            instruments.append(
                Instrument(
                    exchange="ourbit",
                    exchange_symbol=exchange_symbol,
                    canonical_symbol=canonical(base, quote),
                    base_asset=base,
                    quote_asset=quote,
                )
            )

        return instruments

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
                    exchange="ourbit",
                    exchange_symbol=exchange_symbol,
                    canonical_symbol=canonical_symbol,
                    last_price=price,
                    source="rest",
                )
            )

        return ticks

    async def fetch_instruments(self) -> list[Instrument]:
        payload = await get_json(self.url, timeout=self.timeout)
        instruments = self.parse_instruments(payload)
        if not instruments:
            raise RuntimeError("Ourbit response contained no usable USDT perpetual symbols")

        self.symbol_map = {
            item.exchange_symbol: item.canonical_symbol for item in instruments
        }
        return instruments

    async def fetch_prices(self) -> list[PriceTick]:
        payload = await get_json(self.url, timeout=self.timeout)
        ticks = self.parse_prices(payload, self.symbol_map)
        if not ticks:
            raise RuntimeError("Ourbit response contained no usable last prices")
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
                print(f"[ourbit] error: {exc}")
                LOGGER.exception("polling error")
                await error_handler(self.name, exc, attempted_at)

            elapsed = asyncio.get_running_loop().time() - started
            delay = max(0.0, self.poll_seconds - elapsed)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
