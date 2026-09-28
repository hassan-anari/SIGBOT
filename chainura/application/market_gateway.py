from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from chainura.application.health import (HealthObservationRecorder, catalog_observation,
    persist_observations_safely, price_observation)
from chainura.application.signal_service import ReferencePriceResult
from chainura.domain.destination import Exchange
from chainura.domain.monitoring import MarketDecision
from chainura.domain.signal import PriceSourceType, SignalMode, price_source_for_exchange
from chainura.errors import SymbolCatalogError
from market import MarketProbe, READY_STATUSES

PAGE_SIZE = 8
LOGGER = logging.getLogger("chainura.market")


@dataclass(frozen=True, slots=True)
class SymbolContract:
    canonical_symbol: str
    native_symbol: str
    base_asset: str
    quote_asset: str
    exchange: Exchange | None
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
    async def search_exchange(self, exchange: Exchange, query: str, page: int = 1) -> SymbolSearchPage: ...
    async def get_exchange_contract(self, exchange: Exchange, native_symbol: str) -> SymbolContract | None: ...


def _query(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or not value.strip().isascii():
        raise SymbolCatalogError("search query must be non-empty ASCII")
    return value.strip().casefold()


def _page(rows: list[SymbolContract], page: int, needle: str) -> SymbolSearchPage:
    ordered = sorted(rows, key=lambda x: (
        x.canonical_symbol.casefold() != needle and x.native_symbol.casefold() != needle,
        x.canonical_symbol, x.native_symbol))
    pages = max(1, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > pages:
        raise SymbolCatalogError("search page is invalid")
    start = (page - 1) * PAGE_SIZE
    return SymbolSearchPage(tuple(ordered[start:start + PAGE_SIZE]), page, pages)


class SymbolCatalogProvider:
    def __init__(self, probe: MarketProbe, health_repository=None, alert_wake=None) -> None:
        self._probe = probe
        self._health_recorder = HealthObservationRecorder(health_repository, probe.clock, alert_wake) if health_repository is not None else None
        self._discovery_lock = asyncio.Lock()
        self._snapshot_lock = asyncio.Lock()
        self._shared_ready = False
        self._exchange_ready: set[Exchange] = set()
        self.market_service = None

    def _clock(self):
        import datetime as _dt
        return self._probe.clock() if hasattr(self._probe, "clock") else _dt.datetime.now(_dt.timezone.utc)

    async def _record_catalog_results(self, pairs):
        await persist_observations_safely(self._health_recorder, (
            catalog_observation(Exchange(adapter.name), result, self._clock())
            for adapter, result in pairs
        ))

    async def _ensure_discovered(self) -> None:
        if self._shared_ready:
            return
        async with self._discovery_lock:
            if self._shared_ready:
                return
            if self._health_recorder is None and hasattr(self._probe, "discover"):
                try:
                    await self._probe.discover()
                except Exception as exc:
                    self._probe.common_symbols = set(); self._exchange_ready.clear()
                    raise SymbolCatalogError("symbol catalog is unavailable") from exc
            else:
                results = await asyncio.gather(*(adapter.fetch_instruments() for adapter in self._probe.adapters), return_exceptions=True)
                pairs = tuple(zip(self._probe.adapters, results))
                core_errors = [result for adapter, result in pairs
                               if adapter.name in {"ourbit", "toobit", "lbank"} and isinstance(result, Exception)]
                if core_errors:
                    await self._record_catalog_results(zip(self._probe.adapters, results))
                    self._probe.common_symbols = set(); self._exchange_ready.clear()
                    raise SymbolCatalogError("symbol catalog is unavailable")
                for adapter, rows in zip(self._probe.adapters, results):
                    if not isinstance(rows, Exception):
                        self._probe.instruments[adapter.name] = list(rows)
                if hasattr(self._probe, "_exclude_canonical_collisions"):
                    self._probe._exclude_canonical_collisions(); self._probe._filter_lbank_numeric_contracts(); self._probe._rebuild_symbol_maps()
                    from market import COMPOSITE_CORE_EXCHANGES
                    symbol_sets = [{item.canonical_symbol for item in self._probe.instruments[name]} for name in COMPOSITE_CORE_EXCHANGES]
                    self._probe.common_symbols = set.intersection(*symbol_sets) if symbol_sets else set()
                filtered_results = [(adapter, result if isinstance(result, Exception)
                                     else self._probe.instruments.get(adapter.name, ()))
                                    for adapter, result in pairs]
                await self._record_catalog_results(filtered_results)
            required = {"ourbit", "toobit", "lbank"}
            if not required.issubset(self._probe.instruments):
                self._probe.common_symbols = set(); self._exchange_ready.clear()
                raise SymbolCatalogError("symbol catalog is incomplete")
            self._shared_ready = True
            self._exchange_ready.update(Exchange(name) for name in self._probe.instruments)

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
                await persist_observations_safely(self._health_recorder, (catalog_observation(exchange, exc, self._clock()),))
                raise SymbolCatalogError("exchange catalog is unavailable") from exc
            await persist_observations_safely(self._health_recorder, (catalog_observation(exchange, rows, self._clock()),))
            grouped: dict[str, set[str]] = {}
            for row in rows:
                grouped.setdefault(row.canonical_symbol, set()).add(row.exchange_symbol)
            ambiguous = {symbol for symbol, natives in grouped.items() if len(natives) > 1}
            self._probe.instruments[exchange.value] = sorted((row for row in rows if row.canonical_symbol not in ambiguous), key=lambda row: (row.canonical_symbol, row.exchange_symbol))
            adapter.symbol_map = {row.exchange_symbol: row.canonical_symbol for row in self._probe.instruments[exchange.value]}
            self._exchange_ready.add(exchange)

    async def _fetch_prices(self, exchanges: tuple[Exchange, ...]):
        adapters = {Exchange(adapter.name): adapter for adapter in self._probe.adapters}
        ordered = tuple(exchange for exchange in exchanges if exchange in adapters)
        async with self._snapshot_lock:
            fetched = await asyncio.gather(*(adapters[e].fetch_prices() for e in ordered), return_exceptions=True)
            observations = tuple(price_observation(e, response, len(self._probe._expected_symbols(e.value)) if hasattr(self._probe, "_expected_symbols") else 0, self._clock()) for e, response in zip(ordered, fetched))
            raw: dict[Exchange, tuple[object, ...]] = {}
            for exchange, response in zip(ordered, fetched):
                if isinstance(response, Exception):
                    continue
                ticks = tuple(response)
                raw[exchange] = ticks
                for tick in ticks:
                    self._probe.latest[(tick.canonical_symbol, tick.exchange)] = tick
        await persist_observations_safely(self._health_recorder, observations)
        return raw

    async def search_shared(self, query: str, page: int = 1) -> SymbolSearchPage:
        await self._ensure_discovered()
        needle = _query(query)
        mappings = {exchange.value: self._probe._mapping_for(exchange.value)
                    if exchange.value in self._probe.instruments else {} for exchange in Exchange}
        rows = []
        for symbol in self._probe.common_symbols:
            if needle in symbol.casefold():
                item = next(x for x in self._probe.instruments["ourbit"] if x.canonical_symbol == symbol)
                rows.append(SymbolContract(symbol, mappings["ourbit"][symbol], item.base_asset, item.quote_asset, None))
        result = _page(rows, page, needle)
        if self.market_service is not None:
            await self.market_service.refresh_prices(tuple(Exchange))
        else:
            await self._fetch_prices(tuple(Exchange))
        now = self._clock()
        priced = []
        for item in result.items:
            if hasattr(self._probe, "_analyze_symbol"):
                analysis = self._probe._analyze_symbol(item.canonical_symbol, now)
                price = analysis["reference_price"] if analysis["status"] in READY_STATUSES else None
            else:
                price = item.current_price
            priced.append(SymbolContract(item.canonical_symbol, item.native_symbol, item.base_asset, item.quote_asset, item.exchange, price))
        return SymbolSearchPage(tuple(priced), result.page, result.pages)

    async def get_shared(self, canonical_symbol: str) -> SymbolContract | None:
        page = await self.search_shared(canonical_symbol, 1)
        return next((x for x in page.items if x.canonical_symbol == canonical_symbol), None)

    async def search_exchange(self, exchange: Exchange, query: str, page: int = 1) -> SymbolSearchPage:
        await self._ensure_exchange(exchange)
        needle = _query(query)
        rows = [SymbolContract(x.canonical_symbol, x.exchange_symbol, x.base_asset, x.quote_asset, exchange) for x in self._probe.instruments.get(exchange.value, ()) if needle in x.canonical_symbol.casefold() or needle in x.exchange_symbol.casefold()]
        result = _page(rows, page, needle)
        raw = (await self.market_service.refresh_prices((exchange,))) if self.market_service is not None else await self._fetch_prices((exchange,))
        prices = {x.exchange_symbol: x.last_price for x in raw.get(exchange, ()) if isinstance(x.last_price, Decimal) and x.last_price.is_finite() and x.last_price > 0}
        return SymbolSearchPage(tuple(SymbolContract(x.canonical_symbol, x.native_symbol, x.base_asset, x.quote_asset, x.exchange, prices.get(x.native_symbol)) for x in result.items), result.page, result.pages)

    async def get_exchange_contract(self, exchange: Exchange, native_symbol: str) -> SymbolContract | None:
        await self._ensure_exchange(exchange)
        return next((SymbolContract(x.canonical_symbol, x.exchange_symbol, x.base_asset, x.quote_asset, exchange) for x in self._probe.instruments.get(exchange.value, ()) if x.exchange_symbol == native_symbol), None)


class MarketReferencePriceProvider:
    def __init__(self, probe: MarketProbe, catalog: SymbolCatalogProvider | None = None, health_repository=None, alert_wake=None) -> None:
        self._probe = probe
        self._catalog = catalog or SymbolCatalogProvider(probe, health_repository, alert_wake)

    async def get_reference_price(self, *, mode: SignalMode, canonical_symbol: str, selected_exchange: Exchange | None, native_symbol: str | None) -> ReferencePriceResult:
        if mode is SignalMode.COMPOSITE:
            await self._catalog._ensure_discovered()
            if self._catalog.market_service is not None:
                await self._catalog.market_service.refresh_prices(tuple(Exchange))
            else:
                await self._catalog._fetch_prices(tuple(Exchange))
            item = self._probe._analyze_symbol(canonical_symbol, self._catalog._clock())
            mappings = tuple((exchange, self._probe._mapping_for(exchange.value)[canonical_symbol])
                             for exchange in Exchange if exchange.value in self._probe.instruments
                             and canonical_symbol in self._probe._mapping_for(exchange.value))
            prices = tuple((Exchange(name), price) for name, price in sorted(item["prices"].items()))
            return ReferencePriceResult(item["reference_price"], PriceSourceType.COMPOSITE, prices, item["status"] in READY_STATUSES, canonical_symbol, mappings, item.get("reason"))
        if selected_exchange is None or native_symbol is None:
            raise SymbolCatalogError("selected exchange contract is required")
        await self._catalog._ensure_exchange(selected_exchange)
        contract = await self._catalog.get_exchange_contract(selected_exchange, native_symbol)
        if contract is None:
            raise SymbolCatalogError("selected contract is no longer available")
        raw = (await self._catalog.market_service.refresh_prices((selected_exchange,))) if self._catalog.market_service is not None else await self._catalog._fetch_prices((selected_exchange,))
        tick = next((x for x in raw.get(selected_exchange, ()) if x.exchange_symbol == native_symbol), None)
        source = price_source_for_exchange(selected_exchange)
        healthy = tick is not None and isinstance(tick.last_price, Decimal) and tick.last_price.is_finite() and tick.last_price > 0
        return ReferencePriceResult(tick.last_price if healthy else None, source, ((selected_exchange, tick.last_price),) if healthy else (), healthy, contract.canonical_symbol, ((selected_exchange, native_symbol),), None if healthy else "missing_price")


class BatchedMarketSnapshotProvider:
    def __init__(self, probe: MarketProbe, catalog: SymbolCatalogProvider, health_repository=None, alert_wake=None):
        self.probe = probe
        self.catalog = catalog

    async def snapshot(self, signals):
        signals = tuple(signals)
        if not signals:
            return {}
        required: set[Exchange] = set()
        for signal in signals:
            if signal.mode is SignalMode.SINGLE_EXCHANGE and signal.selected_exchange is not None:
                required.add(signal.selected_exchange)
        # Persisted Composite Signals already store their exact exchange/native
        # mappings. Monitoring must not depend on live catalog discovery after a
        # restart; catalog outages affect discovery/search only.
        required.update(exchange for signal in signals if signal.mode is SignalMode.COMPOSITE for exchange, _ in signal.exchange_symbols)
        if not required:
            return {}
        hydrate_durable_symbol_mappings(self.probe, signals)
        if self.catalog.market_service is None:
            # Compatibility for direct/service use outside Runtime. Runtime
            # monitoring consumes the always-on worker's shared cache instead.
            raw_by_exchange = await self.catalog._fetch_prices(tuple(sorted(required, key=lambda x: x.value)))
        else:
            raw_by_exchange = {exchange: tuple(
                tick for (_, tick_exchange), tick in self.probe.latest.items()
                if tick_exchange == exchange.value
            ) for exchange in required}
        now = self.probe.clock(); result = {}
        for signal in signals:
            if signal.mode is SignalMode.COMPOSITE:
                prices = self._composite_prices(signal, raw_by_exchange, now)
                reference = self._composite_reference(prices)
                if reference is None:
                    continue
                item = {"reference_price": reference}
            else:
                selected = signal.selected_exchange
                if selected is None:
                    continue
                native = next((native for exchange, native in signal.exchange_symbols if exchange is selected), None)
                tick = next((x for x in raw_by_exchange.get(selected, ()) if x.exchange == selected.value and x.exchange_symbol == native), None) if selected and native else None
                if tick is None or now - self.probe._require_aware(tick.received_at) > self.probe.max_price_age or not isinstance(tick.last_price, Decimal) or not tick.last_price.is_finite() or tick.last_price <= 0:
                    continue
                item = {"reference_price": tick.last_price}; prices = ((selected, tick.last_price),)
            result[signal.signal_id] = MarketDecision(item["reference_price"], prices, signal.price_source_type, now)
        return result

    def _composite_prices(self, signal, raw_by_exchange, now):
        rows = []
        for exchange, native in signal.exchange_symbols:
            tick = next((x for x in raw_by_exchange.get(exchange, ()) if x.exchange == exchange.value and x.exchange_symbol == native), None)
            if tick is None:
                continue
            if now - self.probe._require_aware(tick.received_at) > self.probe.max_price_age:
                continue
            if not isinstance(tick.last_price, Decimal) or not tick.last_price.is_finite() or tick.last_price <= 0:
                continue
            rows.append((exchange, tick.last_price))
        return tuple(sorted(rows, key=lambda item: item[0].value))

    def _composite_reference(self, prices) -> Decimal | None:
        from market import composite_price_analysis
        result=composite_price_analysis({exchange.value:price for exchange,price in prices},
                                        getattr(self.probe,"max_spread_percent",Decimal("5")))
        return result["reference_price"] if result["status"] in READY_STATUSES else None


def hydrate_durable_symbol_mappings(probe: MarketProbe, signals) -> None:
    """Merge persisted open-Signal mappings without damaging catalog mappings."""
    adapters = {Exchange(adapter.name): adapter for adapter in probe.adapters}
    seen: dict[tuple[Exchange, str], str] = {}
    for signal in signals:
        for exchange, native_symbol in signal.exchange_symbols:
            key = (exchange, native_symbol)
            canonical_symbol = signal.canonical_symbol
            previous = seen.get(key)
            existing = adapters.get(exchange).symbol_map.get(native_symbol) if exchange in adapters else None
            if (previous is not None and previous != canonical_symbol) or (
                existing is not None and existing != canonical_symbol
            ):
                LOGGER.warning(
                    "durable symbol mapping conflict exchange=%s native_symbol=%s; mapping skipped",
                    exchange.value, native_symbol,
                )
                continue
            seen[key] = canonical_symbol
            if exchange in adapters:
                adapters[exchange].symbol_map[native_symbol] = canonical_symbol


class MarketPriceWorker:
    """Runtime-owned, always-on catalog recovery and price polling service."""

    def __init__(self, probe: MarketProbe, catalog: SymbolCatalogProvider,
                 load_work=None, poll_seconds: float = 5.0):
        self.probe = probe
        self.catalog = catalog
        self.load_work = load_work
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self.task = None

    async def start(self):
        if self.task is None or self.task.done():
            self._stop.clear()
            self.task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set(); self._wake.set()
        if self.task is not None:
            await self.task

    async def refresh_prices(self, exchanges: tuple[Exchange, ...] = tuple(Exchange)):
        return await self.catalog._fetch_prices(exchanges)

    async def run_cycle(self):
        signals = await self.load_work(1000) if self.load_work is not None else ()
        try:
            await self.catalog._ensure_discovered()
        except Exception as exc:
            LOGGER.warning("market catalog refresh unavailable category=%s", type(exc).__name__)
        # Discovery rebuilds maps, so durable mappings must be merged afterwards.
        hydrate_durable_symbol_mappings(self.probe, signals)
        # Price failures are converted to per-exchange observations by _fetch_prices;
        # gather(return_exceptions=True) there isolates every adapter.
        return await self.refresh_prices(tuple(Exchange))

    async def _run(self):
        while not self._stop.is_set():
            await self.run_cycle()
            try:
                await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
                self._wake.clear()
            except TimeoutError:
                pass
