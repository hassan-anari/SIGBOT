from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from http_client import get_json
from logging_config import get_logger
from models import Instrument, PriceTick

from .common import canonical, decimal_value, extract_rows, upper

LOGGER = get_logger("exchanges.weex")
SnapshotHandler = Callable[[str, list[PriceTick], datetime], Awaitable[None]]
ErrorHandler = Callable[[str, Exception, datetime], Awaitable[None]]


class WEEXAdapter:
    """Public WEEX Contract API V3 adapter using bulk MARK prices."""

    name = "weex"
    base_url = "https://api-contract.weex.com"

    def __init__(self, *, poll_seconds: float = 5.0, timeout: float = 8.0) -> None:
        self.poll_seconds = poll_seconds
        self.timeout = timeout
        self.symbol_map: dict[str, str] = {}

    @staticmethod
    def _rows(payload: Any, key: str | None = None) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        data = payload.get("data", payload)
        if key and isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
        if isinstance(data, list):
            return data
        return extract_rows(data)

    @classmethod
    def parse_instruments(cls, exchange_info: Any) -> list[Instrument]:
        rows: list[Instrument] = []
        seen: set[str] = set()
        for row in cls._rows(exchange_info, "symbols"):
            if not isinstance(row, dict):
                continue
            symbol = upper(row.get("symbol"))
            base = upper(row.get("baseAsset"))
            quote = upper(row.get("quoteAsset"))
            margin = upper(row.get("marginAsset"))
            contract = upper(row.get("contractType"))
            forward = row.get("forwardContractFlag")
            is_perpetual = contract in {"PERPETUAL", "PERPETUAL_CONTRACT"}
            is_forward = forward is True or str(forward).strip().lower() in {"true", "1"}
            if (not symbol or symbol in seen or not base
                    or quote != "USDT" or margin != "USDT" or not is_perpetual
                    or not is_forward or "TRADIFI" in symbol or not symbol.isascii()):
                continue
            seen.add(symbol)
            rows.append(Instrument("weex", symbol, canonical(base, quote), base, quote))
        return rows

    @staticmethod
    def _timestamp(value: Any) -> datetime | None:
        try:
            if isinstance(value, bool):
                return None
            milliseconds = int(str(value))
            result = datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None
        return result if 2000 <= result.year <= 2100 else None

    @classmethod
    def parse_prices(cls, payload: Any, symbol_map: dict[str, str]) -> list[PriceTick]:
        ticks: list[PriceTick] = []
        envelope_time = payload.get("time", payload.get("timestamp")) if isinstance(payload, dict) else None
        for row in cls._rows(payload):
            if not isinstance(row, dict):
                continue
            native = upper(row.get("symbol"))
            canonical_symbol = symbol_map.get(native)
            price = decimal_value(row.get("markPrice"))
            received_at = cls._timestamp(row.get("time", row.get("timestamp", envelope_time)))
            if (not native or not canonical_symbol or price is None or not price.is_finite()
                    or price <= 0 or received_at is None):
                continue
            ticks.append(PriceTick("weex", native, canonical_symbol, price, received_at, "rest_mark"))
        return ticks

    async def fetch_instruments(self) -> list[Instrument]:
        info = await get_json(
            f"{self.base_url}/capi/v3/market/exchangeInfo",
            timeout=self.timeout,
        )
        instruments = self.parse_instruments(info)
        if not instruments:
            raise RuntimeError("WEEX response contained no usable USDT perpetual symbols")
        self.symbol_map = {item.exchange_symbol: item.canonical_symbol for item in instruments}
        return instruments

    async def fetch_prices(self) -> list[PriceTick]:
        payload = await get_json(f"{self.base_url}/capi/v3/market/premiumIndex", timeout=self.timeout)
        ticks = self.parse_prices(payload, self.symbol_map)
        if not ticks:
            raise RuntimeError("WEEX response contained no usable MARK prices")
        return ticks

    async def run(self, allowed: set[str], snapshot_handler: SnapshotHandler,
                  error_handler: ErrorHandler, stop: asyncio.Event) -> None:
        while not stop.is_set():
            started = asyncio.get_running_loop().time()
            attempted_at = datetime.now(timezone.utc)
            try:
                await snapshot_handler(self.name, await self.fetch_prices(), attempted_at)
            except Exception as exc:
                LOGGER.warning("WEEX polling failed category=%s", type(exc).__name__)
                await error_handler(self.name, exc, attempted_at)
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0, self.poll_seconds - (asyncio.get_running_loop().time() - started)))
            except asyncio.TimeoutError:
                pass
