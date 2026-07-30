from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from exchanges import LBankAdapter, OurbitAdapter, ToobitAdapter
from logging_config import get_logger
from models import (
    ExchangeHealth,
    ExchangeStatus,
    HealthTransition,
    Instrument,
    PriceSnapshot,
    PriceTick,
)

LOGGER = get_logger("market")
EXCHANGES = ("ourbit", "toobit", "lbank")
READY_STATUSES = {"healthy", "degraded"}
DEFAULT_MAX_PRICE_AGE = timedelta(seconds=15)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MarketProbe:
    def __init__(
        self,
        *,
        poll_seconds: float = 5.0,
        max_spread_percent: Decimal = Decimal("5"),
        max_price_age: timedelta = DEFAULT_MAX_PRICE_AGE,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.ourbit = OurbitAdapter(poll_seconds=poll_seconds)
        self.toobit = ToobitAdapter(poll_seconds=poll_seconds)
        self.lbank = LBankAdapter(poll_seconds=poll_seconds)
        self.adapters = [self.ourbit, self.toobit, self.lbank]
        self.max_spread_percent = max_spread_percent
        self.max_price_age = max_price_age
        self.clock = clock
        self.instruments: dict[str, list[Instrument]] = {}
        self.common_symbols: set[str] = set()
        self.latest: dict[tuple[str, str], PriceTick] = {}
        self.collisions: dict[str, dict[str, list[str]]] = {
            exchange: {} for exchange in EXCHANGES
        }
        self.health: dict[str, ExchangeHealth] = {
            exchange: ExchangeHealth(exchange) for exchange in EXCHANGES
        }

    @staticmethod
    def _require_aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    async def discover(self) -> None:
        results = await asyncio.gather(
            *(adapter.fetch_instruments() for adapter in self.adapters),
            return_exceptions=True,
        )
        errors: list[str] = []
        for adapter, result in zip(self.adapters, results):
            if isinstance(result, Exception):
                errors.append(f"{adapter.name}: {result}")
            else:
                self.instruments[adapter.name] = result
        if errors:
            message = "Instrument discovery failed: " + " | ".join(errors)
            LOGGER.error(message)
            raise RuntimeError(message)

        self._exclude_canonical_collisions()
        self._filter_lbank_numeric_contracts()
        self._rebuild_symbol_maps()
        symbol_sets = [
            {item.canonical_symbol for item in self.instruments[adapter.name]}
            for adapter in self.adapters
        ]
        self.common_symbols = set.intersection(*symbol_sets)
        LOGGER.info(
            "discovery completed ourbit=%s toobit=%s lbank=%s common=%s "
            "lbank_raw=%s numeric_ignored=%s numeric_preserved=%s collisions=%s",
            len(self.instruments.get("ourbit", [])),
            len(self.instruments.get("toobit", [])),
            len(self.instruments.get("lbank", [])),
            len(self.common_symbols),
            self.lbank.raw_instrument_count,
            len(self.lbank.ignored_numeric_symbols),
            self.lbank.preserved_numeric_symbols,
            sum(len(rows) for rows in self.collisions.values()),
        )

    def _exclude_canonical_collisions(self) -> None:
        for exchange in EXCHANGES:
            grouped: dict[str, set[str]] = defaultdict(set)
            unique: dict[str, Instrument] = {}
            for item in self.instruments.get(exchange, []):
                grouped[item.canonical_symbol].add(item.exchange_symbol)
                unique.setdefault(item.exchange_symbol, item)
            collisions = {
                canonical: sorted(native_symbols)
                for canonical, native_symbols in sorted(grouped.items())
                if len(native_symbols) > 1
            }
            self.collisions[exchange] = collisions
            for canonical, native_symbols in collisions.items():
                LOGGER.warning(
                    "canonical symbol collision exchange=%s canonical=%s native_count=%s native_symbols=%s",
                    exchange, canonical, len(native_symbols), native_symbols,
                )
            ambiguous = set(collisions)
            self.instruments[exchange] = sorted(
                (item for item in unique.values() if item.canonical_symbol not in ambiguous),
                key=lambda item: (item.canonical_symbol, item.exchange_symbol),
            )

    def _rebuild_symbol_maps(self) -> None:
        for adapter in self.adapters:
            adapter.symbol_map = {
                item.exchange_symbol: item.canonical_symbol
                for item in self.instruments.get(adapter.name, [])
            }

    def _filter_lbank_numeric_contracts(self) -> None:
        ourbit_symbols = {item.canonical_symbol for item in self.instruments.get("ourbit", [])}
        toobit_symbols = {item.canonical_symbol for item in self.instruments.get("toobit", [])}
        candidates = set(self.lbank.numeric_only_candidates)
        preserved = candidates & ourbit_symbols & toobit_symbols
        ignored = candidates - preserved
        self.lbank.preserved_numeric_symbols = sorted(preserved)
        self.lbank.ignored_numeric_symbols = sorted(ignored)
        self.instruments["lbank"] = [
            item for item in self.instruments.get("lbank", [])
            if item.exchange_symbol not in ignored
        ]

    def _expected_symbols(self, exchange: str) -> set[str]:
        mapped = {item.canonical_symbol for item in self.instruments.get(exchange, [])}
        return self.common_symbols & mapped

    def _build_snapshot(
        self, exchange: str, ticks: list[PriceTick], attempted_at: datetime
    ) -> PriceSnapshot:
        attempted_at = self._require_aware(attempted_at)
        expected = self._expected_symbols(exchange)
        valid_by_symbol: dict[str, PriceTick] = {}
        for tick in ticks:
            if tick.exchange == exchange and tick.canonical_symbol in expected and tick.last_price > 0:
                valid_by_symbol[tick.canonical_symbol] = tick
        received = set(valid_by_symbol)
        return PriceSnapshot(
            exchange=exchange,
            attempted_at=attempted_at,
            ticks=tuple(valid_by_symbol[symbol] for symbol in sorted(valid_by_symbol)),
            expected_symbols=frozenset(expected),
            received_symbols=frozenset(received),
            missing_symbols=frozenset(expected - received),
        )

    def _set_health(
        self,
        exchange: str,
        status: ExchangeStatus,
        occurred_at: datetime,
        *,
        expected_count: int,
        received_count: int,
        missing_count: int,
        error: str | None = None,
        observed_at: datetime | None = None,
    ) -> HealthTransition | None:
        occurred_at = self._require_aware(occurred_at)
        observed_at = self._require_aware(observed_at or occurred_at)
        state = self.health[exchange]
        previous = state.status
        unhealthy_duration = None
        state.latest_attempt_at = occurred_at
        state.expected_count = expected_count
        state.received_count = received_count
        state.missing_count = missing_count
        if status in {ExchangeStatus.HEALTHY, ExchangeStatus.DEGRADED}:
            state.latest_success_at = observed_at
            state.last_error = None
            if status is ExchangeStatus.HEALTHY:
                state.latest_complete_at = observed_at
        else:
            state.latest_failure_at = observed_at
            state.last_error = error
        if status in {ExchangeStatus.DEGRADED, ExchangeStatus.DOWN}:
            if state.unhealthy_since is None:
                state.unhealthy_since = observed_at
        elif state.unhealthy_since is not None:
            unhealthy_duration = observed_at - state.unhealthy_since
            state.unhealthy_since = None
        state.status = status
        if previous is status:
            return None
        transition = HealthTransition(
            exchange, previous, status, observed_at, unhealthy_duration
        )
        if status is ExchangeStatus.HEALTHY and previous in {ExchangeStatus.DEGRADED, ExchangeStatus.DOWN}:
            LOGGER.info(
                "exchange recovered exchange=%s previous=%s unhealthy_seconds=%s",
                exchange, previous.value,
                unhealthy_duration.total_seconds() if unhealthy_duration else None,
            )
        elif status is ExchangeStatus.HEALTHY:
            LOGGER.info("exchange health transition exchange=%s previous=%s current=healthy", exchange, previous.value)
        else:
            LOGGER.warning(
                "exchange health transition exchange=%s previous=%s current=%s expected=%s received=%s missing=%s error=%s",
                exchange, previous.value, status.value, expected_count,
                received_count, missing_count, error,
            )
        return transition

    async def _handle_snapshot(
        self, exchange: str, ticks: list[PriceTick], attempted_at: datetime
    ) -> None:
        snapshot = self._build_snapshot(exchange, ticks, attempted_at)
        for tick in snapshot.ticks:
            self.latest[(tick.canonical_symbol, tick.exchange)] = tick
        status = ExchangeStatus.HEALTHY if snapshot.is_complete else ExchangeStatus.DEGRADED
        completed_at = max(
            (self._require_aware(tick.received_at) for tick in snapshot.ticks),
            default=snapshot.attempted_at,
        )
        self._set_health(
            exchange, status, snapshot.attempted_at,
            expected_count=len(snapshot.expected_symbols),
            received_count=len(snapshot.received_symbols),
            missing_count=len(snapshot.missing_symbols),
            observed_at=completed_at,
        )

    async def _handle_failure(
        self, exchange: str, exc: Exception, attempted_at: datetime
    ) -> None:
        expected_count = len(self._expected_symbols(exchange))
        failed_at = max(self._require_aware(self.clock()), self._require_aware(attempted_at))
        self._set_health(
            exchange, ExchangeStatus.DOWN, attempted_at,
            expected_count=expected_count, received_count=0,
            missing_count=expected_count, error=str(exc),
            observed_at=failed_at,
        )

    async def fetch_once(self) -> None:
        attempted_at = self._require_aware(self.clock())
        results = await asyncio.gather(
            *(adapter.fetch_prices() for adapter in self.adapters),
            return_exceptions=True,
        )
        errors: list[str] = []
        for adapter, result in zip(self.adapters, results):
            if isinstance(result, Exception):
                await self._handle_failure(adapter.name, result, attempted_at)
                errors.append(f"{adapter.name}: {result}")
            else:
                await self._handle_snapshot(adapter.name, result, attempted_at)
        if errors:
            message = "Price snapshot failed: " + " | ".join(errors)
            LOGGER.error(message)
            raise RuntimeError(message)

    def _mapping_for(self, exchange: str) -> dict[str, str]:
        return {item.canonical_symbol: item.exchange_symbol for item in self.instruments.get(exchange, [])}

    @staticmethod
    def _spread_percent(first: Decimal, second: Decimal) -> Decimal:
        low, high = min(first, second), max(first, second)
        return ((high - low) / low) * Decimal("100")

    def _analyze_symbol(self, symbol: str, analysis_time: datetime) -> dict[str, Any]:
        prices: dict[str, Decimal] = {}
        missing: list[str] = []
        stale: list[dict[str, Any]] = []
        for exchange in EXCHANGES:
            tick = self.latest.get((symbol, exchange))
            if tick is None:
                missing.append(exchange)
                continue
            received_at = self._require_aware(tick.received_at)
            age = analysis_time - received_at
            if age > self.max_price_age:
                stale.append({
                    "exchange": exchange,
                    "received_at": received_at.isoformat(),
                    "age_seconds": age.total_seconds(),
                })
            elif tick.last_price > 0:
                prices[exchange] = tick.last_price
        result: dict[str, Any] = {
            "symbol": symbol, "prices": prices, "missing": missing, "stale": stale,
            "status": "insufficient", "reference_price": None, "method": None,
            "outlier_source": None, "spread_percent": None, "reason": None,
        }
        if len(prices) < 2:
            result["reason"] = "fewer_than_two_prices"
            return result
        if len(prices) == 2:
            values = list(prices.values())
            spread = self._spread_percent(values[0], values[1])
            result["spread_percent"] = spread
            if spread <= self.max_spread_percent:
                result.update(status="degraded", reference_price=sum(values) / Decimal("2"), method="average_2")
                result["reason"] = "one_source_stale" if stale else "one_source_missing"
            else:
                result.update(status="unresolved", reason="two_sources_disagree")
            return result
        ordered = sorted(prices.items(), key=lambda item: item[1])
        (low_exchange, low), (_, mid), (high_exchange, high) = ordered
        overall = self._spread_percent(low, high)
        result.update(spread_percent=overall, reference_price=mid, method="median_3")
        if overall <= self.max_spread_percent:
            result["status"] = "healthy"
            return result
        low_mid = self._spread_percent(low, mid)
        mid_high = self._spread_percent(mid, high)
        if low_mid <= self.max_spread_percent < mid_high:
            result.update(status="degraded", outlier_source=high_exchange, reason="high_outlier")
        elif mid_high <= self.max_spread_percent < low_mid:
            result.update(status="degraded", outlier_source=low_exchange, reason="low_outlier")
        else:
            result.update(status="unresolved", reference_price=None, method=None, reason="no_agreeing_pair")
        return result

    def analyze_prices(self, analysis_time: datetime | None = None) -> dict[str, Any]:
        analysis_time = self._require_aware(analysis_time or self.clock())
        ready: list[str] = []
        healthy: list[str] = []
        degraded: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []
        missing: dict[str, list[str]] = {}
        stale: dict[str, list[dict[str, Any]]] = {}
        references: list[dict[str, Any]] = []
        unresolved: list[str] = []
        insufficient: list[str] = []
        for symbol in sorted(self.common_symbols):
            item = self._analyze_symbol(symbol, analysis_time)
            status, prices = item["status"], item["prices"]
            if item["missing"]:
                missing[symbol] = item["missing"]
            if item["stale"]:
                stale[symbol] = item["stale"]
            if status in READY_STATUSES:
                ready.append(symbol)
                references.append({
                    "symbol": symbol, "reference_price": str(item["reference_price"]),
                    "method": item["method"], "status": status,
                    "sources": sorted(prices), "outlier_source": item["outlier_source"],
                })
            if status == "healthy": healthy.append(symbol)
            if status == "degraded":
                degraded.append({
                    "symbol": symbol, "reference_price": str(item["reference_price"]),
                    "method": item["method"], "reason": item["reason"],
                    "outlier_source": item["outlier_source"], "missing": item["missing"],
                    "stale": [entry["exchange"] for entry in item["stale"]],
                })
            if status == "unresolved": unresolved.append(symbol)
            if status == "insufficient": insufficient.append(symbol)
            if item["reason"] in {"high_outlier", "low_outlier", "two_sources_disagree", "no_agreeing_pair"}:
                mismatches.append({
                    "symbol": symbol,
                    "prices": {exchange: str(prices[exchange]) for exchange in EXCHANGES if exchange in prices},
                    "spread_percent": str(item["spread_percent"].quantize(Decimal("0.0001"))),
                    "status": status, "reason": item["reason"],
                    "outlier_source": item["outlier_source"],
                    "reference_price": str(item["reference_price"]) if item["reference_price"] is not None else None,
                })
        return {"ready": ready, "healthy": healthy, "degraded": degraded,
                "mismatches": mismatches, "missing": missing, "stale": stale,
                "references": references, "unresolved": unresolved,
                "insufficient": insufficient}

    def price_status(self) -> tuple[list[str], list[dict[str, Any]], dict[str, list[str]]]:
        analysis = self.analyze_prices()
        return analysis["ready"], analysis["mismatches"], analysis["missing"]

    def export_symbols(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        exchange_maps = {exchange: self._mapping_for(exchange) for exchange in EXCHANGES}
        mapping = {symbol: {exchange: exchange_maps[exchange].get(symbol) for exchange in EXCHANGES}
                   for symbol in sorted(self.common_symbols)}
        all_sets = {exchange: {item.canonical_symbol for item in rows}
                    for exchange, rows in self.instruments.items()}
        analysis = self.analyze_prices()
        collision_counts = {exchange: len(self.collisions[exchange]) for exchange in EXCHANGES}
        diagnostics = {
            "counts": {exchange: len(symbols) for exchange, symbols in all_sets.items()},
            "lbank_raw_count": self.lbank.raw_instrument_count,
            "common_count": len(self.common_symbols),
            "reference_ready_count": len(analysis["ready"]),
            "healthy_count": len(analysis["healthy"]),
            "degraded_count": len(analysis["degraded"]),
            "unresolved_count": len(analysis["unresolved"]),
            "insufficient_count": len(analysis["insufficient"]),
            "price_mismatch_count": len(analysis["mismatches"]),
            "missing_price_count": len(analysis["missing"]),
            "stale_price_count": len(analysis["stale"]),
            "stale_price_entry_count": sum(len(v) for v in analysis["stale"].values()),
            "collision_count": sum(collision_counts.values()),
            "collision_counts": collision_counts,
            "max_spread_percent": str(self.max_spread_percent),
            "max_price_age_seconds": self.max_price_age.total_seconds(),
            "only_ourbit": sorted(all_sets["ourbit"] - all_sets["toobit"] - all_sets["lbank"]),
            "only_toobit": sorted(all_sets["toobit"] - all_sets["ourbit"] - all_sets["lbank"]),
            "only_lbank": sorted(all_sets["lbank"] - all_sets["ourbit"] - all_sets["toobit"]),
            "ourbit_toobit_only": sorted((all_sets["ourbit"] & all_sets["toobit"]) - all_sets["lbank"]),
            "ourbit_lbank_only": sorted((all_sets["ourbit"] & all_sets["lbank"]) - all_sets["toobit"]),
            "toobit_lbank_only": sorted((all_sets["toobit"] & all_sets["lbank"]) - all_sets["ourbit"]),
            "lbank_numeric_candidate_count": len(self.lbank.numeric_only_candidates),
            "lbank_ignored_numeric_count": len(self.lbank.ignored_numeric_symbols),
            "lbank_preserved_numeric_symbols": self.lbank.preserved_numeric_symbols,
        }
        values = {
            "common_symbols.json": sorted(self.common_symbols), "symbol_mapping.json": mapping,
            "symbol_diagnostics.json": diagnostics, "price_ready_symbols.json": analysis["ready"],
            "healthy_symbols.json": analysis["healthy"], "degraded_symbols.json": analysis["degraded"],
            "reference_prices.json": analysis["references"], "price_mismatches.json": analysis["mismatches"],
            "missing_prices.json": analysis["missing"], "stale_prices.json": analysis["stale"],
            "symbol_collisions.json": self.collisions,
            "lbank_ignored_numeric_symbols.json": self.lbank.ignored_numeric_symbols,
        }
        for name, value in values.items(): self._write_json(output_dir / name, value)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def print_snapshot(self, limit: int = 10) -> None:
        analysis = self.analyze_prices()
        mismatch_by_symbol = {item["symbol"]: item for item in analysis["mismatches"]}
        reference_by_symbol = {item["symbol"]: item for item in analysis["references"]}
        print("\n--- latest REST prices (in memory only) ---")
        for symbol in sorted(self.common_symbols)[:limit]:
            values = [f"{exchange}={self.latest[(symbol, exchange)].last_price if (symbol, exchange) in self.latest else '-'}" for exchange in EXCHANGES]
            suffix = ""
            if symbol in mismatch_by_symbol:
                mismatch = mismatch_by_symbol[symbol]
                suffix = f"  ! {mismatch['status']} spread={mismatch['spread_percent']}% outlier={mismatch['outlier_source']} ref={mismatch['reference_price']}"
            elif symbol in analysis["stale"]:
                suffix = "  ? stale=" + ",".join(row["exchange"] for row in analysis["stale"][symbol])
            elif symbol in analysis["missing"]:
                suffix = "  ? missing=" + ",".join(analysis["missing"][symbol])
            elif symbol in reference_by_symbol:
                suffix = f"  ref={reference_by_symbol[symbol]['reference_price']}"
            print(f"{symbol}: " + " | ".join(values) + suffix)
        statuses = ",".join(f"{name}={self.health[name].status.value}" for name in EXCHANGES)
        print(f"ticks in memory: {len(self.latest)} / {len(self.common_symbols) * 3}")
        print(f"reference-ready: {len(analysis['ready'])} | healthy: {len(analysis['healthy'])} | degraded: {len(analysis['degraded'])} | unresolved: {len(analysis['unresolved'])} | insufficient: {len(analysis['insufficient'])} | missing: {len(analysis['missing'])} | stale: {len(analysis['stale'])}")
        print(f"exchange health: {statuses}")
        LOGGER.info("health ticks=%s/%s reference_ready=%s healthy=%s degraded=%s unresolved=%s insufficient=%s missing=%s stale=%s exchanges=%s", len(self.latest), len(self.common_symbols) * 3, len(analysis["ready"]), len(analysis["healthy"]), len(analysis["degraded"]), len(analysis["unresolved"]), len(analysis["insufficient"]), len(analysis["missing"]), len(analysis["stale"]), statuses)

    async def watch(self, *, seconds: int | None = None, print_every: float = 5.0) -> None:
        if not self.common_symbols: await self.discover()
        LOGGER.info("watch started common_symbols=%s seconds=%s print_every=%s", len(self.common_symbols), seconds, print_every)
        stop = asyncio.Event()
        async def reporter() -> None:
            while not stop.is_set():
                try: await asyncio.wait_for(stop.wait(), timeout=print_every)
                except asyncio.TimeoutError: self.print_snapshot()
        tasks = [asyncio.create_task(adapter.run(self.common_symbols, self._handle_snapshot, self._handle_failure, stop)) for adapter in self.adapters]
        tasks.append(asyncio.create_task(reporter()))
        try:
            if seconds is None: await asyncio.gather(*tasks)
            else: await asyncio.sleep(seconds)
        finally:
            stop.set()
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.print_snapshot()
            LOGGER.info("watch stopped")
