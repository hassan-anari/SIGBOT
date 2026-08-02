from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from chainura.application.signal_service import ReferencePriceResult
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType, SignalMode
from chainura.errors import SymbolCatalogError
from market import MarketProbe, READY_STATUSES

PAGE_SIZE = 8


@dataclass(frozen=True, slots=True)
class SymbolContract:
    canonical_symbol: str
    native_symbol: str
    base_asset: str
    quote_asset: str
    exchange: Exchange | None


@dataclass(frozen=True, slots=True)
class SymbolSearchPage:
    items: tuple[SymbolContract, ...]
    page: int
    pages: int


class SharedSymbolCatalog(Protocol):
    async def search_shared(self, query: str, page: int = 1) -> SymbolSearchPage: ...
    async def get_shared(self, canonical_symbol: str) -> SymbolContract | None: ...


class ExchangeSymbolCatalog(Protocol):
    async def search_exchange(self, exchange: Exchange, query: str,
                              page: int = 1) -> SymbolSearchPage: ...
    async def get_exchange_contract(self, exchange: Exchange,
                                    native_symbol: str) -> SymbolContract | None: ...


class SymbolCatalogProvider:
    """Application adapter over the existing MarketProbe and exchange adapters."""

    def __init__(self, probe: MarketProbe) -> None:
        self._probe = probe
        self._discovery_lock = asyncio.Lock()

    async def _ensure_discovered(self) -> None:
        if self._probe.instruments:
            return
        async with self._discovery_lock:
            if not self._probe.instruments:
                try:
                    await self._probe.discover()
                except Exception as exc:
                    raise SymbolCatalogError("symbol catalog is unavailable") from exc

    async def _ensure_exchange(self, exchange: Exchange) -> None:
        if exchange.value in self._probe.instruments:
            return
        async with self._discovery_lock:
            if exchange.value in self._probe.instruments:
                return
            adapter = next((x for x in self._probe.adapters if x.name == exchange.value), None)
            if adapter is None:
                raise SymbolCatalogError("exchange catalog is unavailable")
            try:
                rows = await adapter.fetch_instruments()
            except Exception as exc:
                raise SymbolCatalogError("exchange catalog is unavailable") from exc
            grouped: dict[str, set[str]] = {}
            for row in rows:
                grouped.setdefault(row.canonical_symbol, set()).add(row.exchange_symbol)
            ambiguous = {symbol for symbol,natives in grouped.items() if len(natives) > 1}
            self._probe.instruments[exchange.value] = sorted(
                (row for row in rows if row.canonical_symbol not in ambiguous),
                key=lambda row:(row.canonical_symbol,row.exchange_symbol))
            adapter.symbol_map = {row.exchange_symbol:row.canonical_symbol
                                  for row in self._probe.instruments[exchange.value]}

    async def search_shared(self, query: str, page: int = 1) -> SymbolSearchPage:
        await self._ensure_discovered()
        needle = _query(query)
        mappings = {name: self._probe._mapping_for(name) for name in ("ourbit", "toobit", "lbank")}
        rows = []
        for symbol in self._probe.common_symbols:
            if needle in symbol.casefold():
                item = next(x for x in self._probe.instruments["ourbit"] if x.canonical_symbol == symbol)
                rows.append(SymbolContract(symbol, mappings["ourbit"][symbol], item.base_asset, item.quote_asset, None))
        return _page(rows, page, needle)

    async def get_shared(self, canonical_symbol: str) -> SymbolContract | None:
        page = await self.search_shared(canonical_symbol, 1)
        return next((x for x in page.items if x.canonical_symbol == canonical_symbol), None)

    async def search_exchange(self, exchange: Exchange, query: str, page: int = 1) -> SymbolSearchPage:
        await self._ensure_exchange(exchange)
        needle = _query(query)
        rows = [SymbolContract(x.canonical_symbol, x.exchange_symbol, x.base_asset, x.quote_asset, exchange)
                for x in self._probe.instruments.get(exchange.value, ())
                if needle in x.canonical_symbol.casefold() or needle in x.exchange_symbol.casefold()]
        return _page(rows, page, needle)

    async def get_exchange_contract(self, exchange: Exchange, native_symbol: str) -> SymbolContract | None:
        await self._ensure_exchange(exchange)
        return next((SymbolContract(x.canonical_symbol,x.exchange_symbol,x.base_asset,x.quote_asset,exchange)
                     for x in self._probe.instruments.get(exchange.value,()) if x.exchange_symbol == native_symbol), None)


class MarketReferencePriceProvider:
    def __init__(self, probe: MarketProbe) -> None:
        self._probe = probe
        self._catalog = SymbolCatalogProvider(probe)

    async def get_reference_price(self, *, mode: SignalMode, canonical_symbol: str,
            selected_exchange: Exchange | None, native_symbol: str | None) -> ReferencePriceResult:
        if mode is SignalMode.COMPOSITE:
            await self._catalog._ensure_discovered()
            try:
                await self._probe.fetch_once()
            except RuntimeError:
                # MarketProbe has already recorded successful snapshots and failures.
                pass
            item = self._probe._analyze_symbol(canonical_symbol, self._probe.clock())
            mappings = tuple((exchange, self._probe._mapping_for(exchange.value)[canonical_symbol])
                             for exchange in Exchange if canonical_symbol in self._probe._mapping_for(exchange.value))
            prices = tuple((Exchange(name), price) for name,price in sorted(item["prices"].items()))
            return ReferencePriceResult(item["reference_price"], PriceSourceType.COMPOSITE, prices,
                item["status"] in READY_STATUSES, canonical_symbol, mappings, item.get("reason"))
        if selected_exchange is None or native_symbol is None:
            raise SymbolCatalogError("selected exchange contract is required")
        await self._catalog._ensure_exchange(selected_exchange)
        contract = await self._catalog.get_exchange_contract(selected_exchange, native_symbol)
        if contract is None:
            raise SymbolCatalogError("selected contract is no longer available")
        adapter = next(x for x in self._probe.adapters if x.name == selected_exchange.value)
        ticks = await adapter.fetch_prices()
        tick = next((x for x in ticks if x.exchange_symbol == native_symbol), None)
        source = {Exchange.OURBIT:PriceSourceType.OURBIT_ONLY,Exchange.TOOBIT:PriceSourceType.TOOBIT_ONLY,Exchange.LBANK:PriceSourceType.LBANK_ONLY}[selected_exchange]
        healthy = tick is not None and isinstance(tick.last_price,Decimal) and tick.last_price.is_finite() and tick.last_price > 0
        return ReferencePriceResult(tick.last_price if healthy else None,source,
            ((selected_exchange,tick.last_price),) if healthy else (),healthy,contract.canonical_symbol,
            ((selected_exchange,native_symbol),),None if healthy else "missing_price")


def _query(value: str) -> str:
    if not isinstance(value,str) or not value.strip() or not value.strip().isascii():
        raise SymbolCatalogError("search query must be non-empty ASCII")
    return value.strip().casefold()


def _page(rows: list[SymbolContract], page: int, needle: str) -> SymbolSearchPage:
    ordered = sorted(rows, key=lambda x: (
        x.canonical_symbol.casefold() != needle and x.native_symbol.casefold() != needle,
        x.canonical_symbol, x.native_symbol))
    pages = max(1, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
    if isinstance(page,bool) or not isinstance(page,int) or page < 1 or page > pages:
        raise SymbolCatalogError("search page is invalid")
    start = (page - 1) * PAGE_SIZE
    return SymbolSearchPage(tuple(ordered[start:start+PAGE_SIZE]),page,pages)
