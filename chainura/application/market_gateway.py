from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from chainura.application.signal_service import ReferencePriceResult
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType, SignalMode
from chainura.errors import SymbolCatalogError
from chainura.domain.monitoring import MarketDecision
from chainura.domain.health import HealthObservation, ServiceHealthStatus, ApiOperation
from datetime import timezone
from market import MarketProbe, READY_STATUSES

PAGE_SIZE = 8


@dataclass(frozen=True, slots=True)
class SymbolContract:
    canonical_symbol: str
    native_symbol: str
    base_asset: str
    quote_asset: str
    exchange: Exchange | None
    # Compatibility default for identity-only catalog fakes; production search
    # always supplies an explicit current price or ``None``.
    current_price: Decimal | None = Decimal("1")


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

    def __init__(self, probe: MarketProbe, health_repository=None) -> None:
        self._probe = probe
        self._health_repository = health_repository
        self._discovery_lock = asyncio.Lock()
        self._snapshot_lock = asyncio.Lock()
        self._shared_ready = False
        self._exchange_ready: set[Exchange] = set()

    async def _ensure_discovered(self) -> None:
        if self._shared_ready:
            return
        async with self._discovery_lock:
            if self._shared_ready:
                return
            try:
                await self._probe.discover()
            except Exception as exc:
                # MarketProbe may retain successful partial responses. They are not a
                # completed shared catalog and the next request must retry discovery.
                self._probe.common_symbols = set()
                self._exchange_ready.clear()
                raise SymbolCatalogError("symbol catalog is unavailable") from exc
            required = {exchange.value for exchange in Exchange}
            if not required.issubset(self._probe.instruments):
                self._probe.common_symbols = set()
                self._exchange_ready.clear()
                raise SymbolCatalogError("symbol catalog is incomplete")
            self._shared_ready = True
            self._exchange_ready.update(Exchange)

    async def _ensure_exchange(self, exchange: Exchange) -> None:
        if exchange in self._exchange_ready:
            return
        async with self._discovery_lock:
            if exchange in self._exchange_ready:
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
            self._exchange_ready.add(exchange)

    async def search_shared(self, query: str, page: int = 1) -> SymbolSearchPage:
        await self._ensure_discovered()
        needle = _query(query)
        mappings = {name: self._probe._mapping_for(name) for name in ("ourbit", "toobit", "lbank")}
        rows = []
        for symbol in self._probe.common_symbols:
            if needle in symbol.casefold():
                item = next(x for x in self._probe.instruments["ourbit"] if x.canonical_symbol == symbol)
                rows.append(SymbolContract(symbol, mappings["ourbit"][symbol], item.base_asset, item.quote_asset, None))
        result=_page(rows, page, needle)
        if not hasattr(self._probe,"fetch_once"):
            return result
        async with self._snapshot_lock:
            try:await self._probe.fetch_once()
            except RuntimeError:pass
            now=self._probe.clock()
            priced=[]
            for item in result.items:
                analysis=self._probe._analyze_symbol(item.canonical_symbol,now)
                price=analysis["reference_price"] if analysis["status"] in READY_STATUSES else None
                priced.append(SymbolContract(item.canonical_symbol,item.native_symbol,item.base_asset,item.quote_asset,item.exchange,price))
        return SymbolSearchPage(tuple(priced),result.page,result.pages)

    async def get_shared(self, canonical_symbol: str) -> SymbolContract | None:
        page = await self.search_shared(canonical_symbol, 1)
        return next((x for x in page.items if x.canonical_symbol == canonical_symbol), None)

    async def search_exchange(self, exchange: Exchange, query: str, page: int = 1) -> SymbolSearchPage:
        await self._ensure_exchange(exchange)
        needle = _query(query)
        rows = [SymbolContract(x.canonical_symbol, x.exchange_symbol, x.base_asset, x.quote_asset, exchange)
                for x in self._probe.instruments.get(exchange.value, ())
                if needle in x.canonical_symbol.casefold() or needle in x.exchange_symbol.casefold()]
        result=_page(rows,page,needle)
        adapter=next((x for x in self._probe.adapters if x.name==exchange.value),None)
        if adapter is None:return result
        async with self._snapshot_lock:
            try:ticks=await adapter.fetch_prices()
            except Exception:ticks=()
            prices={x.exchange_symbol:x.last_price for x in ticks if isinstance(x.last_price,Decimal) and x.last_price.is_finite() and x.last_price>0}
        return SymbolSearchPage(tuple(SymbolContract(x.canonical_symbol,x.native_symbol,x.base_asset,x.quote_asset,x.exchange,prices.get(x.native_symbol)) for x in result.items),result.page,result.pages)

    async def get_exchange_contract(self, exchange: Exchange, native_symbol: str) -> SymbolContract | None:
        await self._ensure_exchange(exchange)
        return next((SymbolContract(x.canonical_symbol,x.exchange_symbol,x.base_asset,x.quote_asset,exchange)
                     for x in self._probe.instruments.get(exchange.value,()) if x.exchange_symbol == native_symbol), None)


class MarketReferencePriceProvider:
    def __init__(self, probe: MarketProbe,
                 catalog: SymbolCatalogProvider | None = None, health_repository=None) -> None:
        self._probe = probe
        self._health_repository = health_repository
        self._catalog = catalog or SymbolCatalogProvider(probe, health_repository)

    async def get_reference_price(self, *, mode: SignalMode, canonical_symbol: str,
            selected_exchange: Exchange | None, native_symbol: str | None) -> ReferencePriceResult:
        if mode is SignalMode.COMPOSITE:
            await self._catalog._ensure_discovered()
            async with self._catalog._snapshot_lock:
                try:await self._probe.fetch_once()
                except RuntimeError:pass
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
        async with self._catalog._snapshot_lock:ticks = await adapter.fetch_prices()
        tick = next((x for x in ticks if x.exchange_symbol == native_symbol), None)
        source = {Exchange.OURBIT:PriceSourceType.OURBIT_ONLY,Exchange.TOOBIT:PriceSourceType.TOOBIT_ONLY,Exchange.LBANK:PriceSourceType.LBANK_ONLY}[selected_exchange]
        healthy = tick is not None and isinstance(tick.last_price,Decimal) and tick.last_price.is_finite() and tick.last_price > 0
        return ReferencePriceResult(tick.last_price if healthy else None,source,
            ((selected_exchange,tick.last_price),) if healthy else (),healthy,contract.canonical_symbol,
            ((selected_exchange,native_symbol),),None if healthy else "missing_price")


class BatchedMarketSnapshotProvider:
    """One coherent per-cycle exchange refresh for composite and native signals."""
    def __init__(self,probe:MarketProbe,catalog:SymbolCatalogProvider,health_repository=None):self.probe=probe;self.catalog=catalog;self.health_repository=health_repository
    async def snapshot(self,signals):
        signals=tuple(signals)
        if not signals:return {}
        has_composite=any(signal.mode is SignalMode.COMPOSITE for signal in signals)
        shared_ready=not has_composite
        if has_composite:
            try:
                await self.catalog._ensure_discovered()
                shared_ready=True
            except SymbolCatalogError:
                shared_ready=False
        single_ready:set[Exchange]=set()
        for signal in signals:
            if signal.mode is SignalMode.SINGLE_EXCHANGE and signal.selected_exchange is not None:
                try:
                    await self.catalog._ensure_exchange(signal.selected_exchange)
                    single_ready.add(signal.selected_exchange)
                except SymbolCatalogError:
                    continue
        async with self.catalog._snapshot_lock:
            adapters={Exchange(adapter.name):adapter for adapter in self.probe.adapters}
            required=set(single_ready)
            if shared_ready:
                required.update(exchange for signal in signals if signal.mode is SignalMode.COMPOSITE for exchange,_ in signal.exchange_symbols)
            if not required:return {}
            ordered=tuple(e for e in sorted(required,key=lambda x:x.value) if e in adapters)
            fetched=await asyncio.gather(*(adapters[e].fetch_prices() for e in ordered),return_exceptions=True)
            raw_by_exchange={}
            if self.health_repository is not None:
                for exchange,response in zip(ordered,fetched):
                    if isinstance(response,Exception):
                        await self.health_repository.observe(HealthObservation(f"{exchange.value}_prices",exchange,ApiOperation.PRICES,ServiceHealthStatus.DOWN,now=self.probe.clock() if False else __import__("datetime").datetime.now(timezone.utc),error_category="network"))
                    else:
                        ticks=tuple(response); usable=tuple(x for x in ticks if isinstance(x.last_price,Decimal) and x.last_price.is_finite() and x.last_price>0)
                        status=ServiceHealthStatus.HEALTHY if usable and len(usable)==len(ticks) else (ServiceHealthStatus.DEGRADED if usable else ServiceHealthStatus.DOWN)
                        await self.health_repository.observe(HealthObservation(f"{exchange.value}_prices",exchange,ApiOperation.PRICES,status,__import__("datetime").datetime.now(timezone.utc),None if status is not ServiceHealthStatus.DOWN else "empty_response"))
            for exchange,response in zip(ordered,fetched):
                if isinstance(response,Exception):
                    continue
                ticks=tuple(response)
                raw_by_exchange[exchange]=ticks
                for tick in ticks:
                    self.probe.latest[(tick.canonical_symbol,tick.exchange)]=tick
            now=self.probe.clock();result={}
            for signal in signals:
                if signal.mode is SignalMode.COMPOSITE:
                    if not shared_ready:continue
                    item=self.probe._analyze_symbol(signal.canonical_symbol,now)
                    if item["status"] not in READY_STATUSES:continue
                    prices=tuple((Exchange(name),price) for name,price in sorted(item["prices"].items()))
                else:
                    selected=signal.selected_exchange
                    if selected not in single_ready:continue
                    native=next((native for exchange,native in signal.exchange_symbols if exchange is selected),None)
                    tick=next((x for x in raw_by_exchange.get(selected,()) if x.exchange==selected.value and x.exchange_symbol==native),None) if selected and native else None
                    if tick is None or now-self.probe._require_aware(tick.received_at)>self.probe.max_price_age or not isinstance(tick.last_price,Decimal) or not tick.last_price.is_finite() or tick.last_price<=0:continue
                    item={"reference_price":tick.last_price};prices=((selected,tick.last_price),)
                result[signal.signal_id]=MarketDecision(item["reference_price"],prices,signal.price_source_type,now)
            return result


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
