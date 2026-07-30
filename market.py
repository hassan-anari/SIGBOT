from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from exchanges import LBankAdapter, OurbitAdapter, ToobitAdapter
from logging_config import get_logger
from models import Instrument, PriceTick

LOGGER = get_logger("market")

EXCHANGES = ("ourbit", "toobit", "lbank")
READY_STATUSES = {"healthy", "degraded"}


class MarketProbe:
    def __init__(
        self,
        *,
        poll_seconds: float = 5.0,
        max_spread_percent: Decimal = Decimal("5"),
    ) -> None:
        self.ourbit = OurbitAdapter(poll_seconds=poll_seconds)
        self.toobit = ToobitAdapter(poll_seconds=poll_seconds)
        self.lbank = LBankAdapter(poll_seconds=poll_seconds)
        self.adapters = [self.ourbit, self.toobit, self.lbank]
        self.max_spread_percent = max_spread_percent

        self.instruments: dict[str, list[Instrument]] = {}
        self.common_symbols: set[str] = set()
        self.latest: dict[tuple[str, str], PriceTick] = {}

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

        self._filter_lbank_numeric_contracts()

        symbol_sets = [
            {item.canonical_symbol for item in self.instruments[adapter.name]}
            for adapter in self.adapters
        ]
        self.common_symbols = set.intersection(*symbol_sets)
        LOGGER.info(
            "discovery completed ourbit=%s toobit=%s lbank=%s common=%s "
            "lbank_raw=%s numeric_ignored=%s numeric_preserved=%s",
            len(self.instruments.get("ourbit", [])),
            len(self.instruments.get("toobit", [])),
            len(self.instruments.get("lbank", [])),
            len(self.common_symbols),
            self.lbank.raw_instrument_count,
            len(self.lbank.ignored_numeric_symbols),
            self.lbank.preserved_numeric_symbols,
        )

    def _filter_lbank_numeric_contracts(self) -> None:
        """Remove numeric-only LBank symbols unless both other exchanges have them."""
        ourbit_symbols = {
            item.canonical_symbol for item in self.instruments.get("ourbit", [])
        }
        toobit_symbols = {
            item.canonical_symbol for item in self.instruments.get("toobit", [])
        }
        candidates = set(self.lbank.numeric_only_candidates)

        preserved = candidates & ourbit_symbols & toobit_symbols
        ignored = candidates - preserved

        self.lbank.preserved_numeric_symbols = sorted(preserved)
        self.lbank.ignored_numeric_symbols = sorted(ignored)
        self.instruments["lbank"] = [
            item
            for item in self.instruments.get("lbank", [])
            if item.exchange_symbol not in ignored
        ]
        self.lbank.symbol_map = {
            item.exchange_symbol: item.canonical_symbol
            for item in self.instruments["lbank"]
        }

    async def fetch_once(self) -> None:
        results = await asyncio.gather(
            *(adapter.fetch_prices() for adapter in self.adapters),
            return_exceptions=True,
        )

        errors: list[str] = []
        for adapter, result in zip(self.adapters, results):
            if isinstance(result, Exception):
                errors.append(f"{adapter.name}: {result}")
                continue

            for tick in result:
                if tick.canonical_symbol in self.common_symbols:
                    self.latest[(tick.canonical_symbol, tick.exchange)] = tick

        if errors:
            message = "Price snapshot failed: " + " | ".join(errors)
            LOGGER.error(message)
            raise RuntimeError(message)

    async def _handle_tick(self, tick: PriceTick) -> None:
        self.latest[(tick.canonical_symbol, tick.exchange)] = tick

    def _mapping_for(self, exchange: str) -> dict[str, str]:
        return {
            item.canonical_symbol: item.exchange_symbol
            for item in self.instruments.get(exchange, [])
        }

    @staticmethod
    def _spread_percent(first: Decimal, second: Decimal) -> Decimal:
        low = min(first, second)
        high = max(first, second)
        return ((high - low) / low) * Decimal("100")

    def _analyze_symbol(self, symbol: str) -> dict[str, Any]:
        prices: dict[str, Decimal] = {}
        missing_exchanges: list[str] = []

        for exchange in EXCHANGES:
            tick = self.latest.get((symbol, exchange))
            if tick is None:
                missing_exchanges.append(exchange)
            else:
                prices[exchange] = tick.last_price

        result: dict[str, Any] = {
            "symbol": symbol,
            "prices": prices,
            "missing": missing_exchanges,
            "status": "insufficient",
            "reference_price": None,
            "method": None,
            "outlier_source": None,
            "spread_percent": None,
            "reason": None,
        }

        if len(prices) < 2:
            result["reason"] = "fewer_than_two_prices"
            return result

        if len(prices) == 2:
            values = list(prices.values())
            spread = self._spread_percent(values[0], values[1])
            result["spread_percent"] = spread
            if spread <= self.max_spread_percent:
                result["status"] = "degraded"
                result["reference_price"] = sum(values) / Decimal("2")
                result["method"] = "average_2"
                result["reason"] = "one_source_missing"
            else:
                result["status"] = "unresolved"
                result["reason"] = "two_sources_disagree"
            return result

        ordered = sorted(prices.items(), key=lambda item: item[1])
        (low_exchange, low), (mid_exchange, mid), (high_exchange, high) = ordered
        overall_spread = self._spread_percent(low, high)
        result["spread_percent"] = overall_spread
        result["reference_price"] = mid
        result["method"] = "median_3"

        if overall_spread <= self.max_spread_percent:
            result["status"] = "healthy"
            return result

        low_mid_spread = self._spread_percent(low, mid)
        mid_high_spread = self._spread_percent(mid, high)

        if low_mid_spread <= self.max_spread_percent < mid_high_spread:
            result["status"] = "degraded"
            result["outlier_source"] = high_exchange
            result["reason"] = "high_outlier"
            return result

        if mid_high_spread <= self.max_spread_percent < low_mid_spread:
            result["status"] = "degraded"
            result["outlier_source"] = low_exchange
            result["reason"] = "low_outlier"
            return result

        result["status"] = "unresolved"
        result["reference_price"] = None
        result["method"] = None
        result["reason"] = "no_agreeing_pair"
        return result

    def analyze_prices(self) -> dict[str, list[Any]]:
        ready: list[str] = []
        healthy: list[str] = []
        degraded: list[dict[str, Any]] = []
        mismatches: list[dict[str, Any]] = []
        missing: dict[str, list[str]] = {}
        references: list[dict[str, Any]] = []
        unresolved: list[str] = []
        insufficient: list[str] = []

        for symbol in sorted(self.common_symbols):
            analysis = self._analyze_symbol(symbol)
            status = analysis["status"]
            prices = analysis["prices"]
            missing_exchanges = analysis["missing"]

            if missing_exchanges:
                missing[symbol] = missing_exchanges

            if status in READY_STATUSES:
                ready.append(symbol)
                references.append(
                    {
                        "symbol": symbol,
                        "reference_price": str(analysis["reference_price"]),
                        "method": analysis["method"],
                        "status": status,
                        "sources": sorted(prices),
                        "outlier_source": analysis["outlier_source"],
                    }
                )

            if status == "healthy":
                healthy.append(symbol)

            if status == "degraded":
                degraded.append(
                    {
                        "symbol": symbol,
                        "reference_price": str(analysis["reference_price"]),
                        "method": analysis["method"],
                        "reason": analysis["reason"],
                        "outlier_source": analysis["outlier_source"],
                        "missing": missing_exchanges,
                    }
                )

            if status == "unresolved":
                unresolved.append(symbol)

            if status == "insufficient":
                insufficient.append(symbol)

            if analysis["reason"] in {
                "high_outlier",
                "low_outlier",
                "two_sources_disagree",
                "no_agreeing_pair",
            }:
                mismatches.append(
                    {
                        "symbol": symbol,
                        "prices": {
                            exchange: str(prices[exchange])
                            for exchange in EXCHANGES
                            if exchange in prices
                        },
                        "spread_percent": str(
                            analysis["spread_percent"].quantize(Decimal("0.0001"))
                        ),
                        "status": status,
                        "reason": analysis["reason"],
                        "outlier_source": analysis["outlier_source"],
                        "reference_price": (
                            str(analysis["reference_price"])
                            if analysis["reference_price"] is not None
                            else None
                        ),
                    }
                )

        return {
            "ready": ready,
            "healthy": healthy,
            "degraded": degraded,
            "mismatches": mismatches,
            "missing": missing,
            "references": references,
            "unresolved": unresolved,
            "insufficient": insufficient,
        }

    def price_status(self) -> tuple[list[str], list[dict[str, Any]], dict[str, list[str]]]:
        """Compatibility helper used by the CLI and tests."""
        analysis = self.analyze_prices()
        return analysis["ready"], analysis["mismatches"], analysis["missing"]

    def export_symbols(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)

        exchange_maps = {
            exchange: self._mapping_for(exchange) for exchange in EXCHANGES
        }
        mapping = {
            symbol: {
                exchange: exchange_maps[exchange].get(symbol)
                for exchange in EXCHANGES
            }
            for symbol in sorted(self.common_symbols)
        }

        all_sets = {
            exchange: {item.canonical_symbol for item in rows}
            for exchange, rows in self.instruments.items()
        }
        analysis = self.analyze_prices()

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
            "max_spread_percent": str(self.max_spread_percent),
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

        self._write_json(output_dir / "common_symbols.json", sorted(self.common_symbols))
        self._write_json(output_dir / "symbol_mapping.json", mapping)
        self._write_json(output_dir / "symbol_diagnostics.json", diagnostics)
        self._write_json(output_dir / "price_ready_symbols.json", analysis["ready"])
        self._write_json(output_dir / "healthy_symbols.json", analysis["healthy"])
        self._write_json(output_dir / "degraded_symbols.json", analysis["degraded"])
        self._write_json(output_dir / "reference_prices.json", analysis["references"])
        self._write_json(output_dir / "price_mismatches.json", analysis["mismatches"])
        self._write_json(output_dir / "missing_prices.json", analysis["missing"])
        self._write_json(
            output_dir / "lbank_ignored_numeric_symbols.json",
            self.lbank.ignored_numeric_symbols,
        )

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def print_snapshot(self, limit: int = 10) -> None:
        analysis = self.analyze_prices()
        mismatch_by_symbol = {
            item["symbol"]: item for item in analysis["mismatches"]
        }
        reference_by_symbol = {
            item["symbol"]: item for item in analysis["references"]
        }

        print("\n--- latest REST prices (in memory only) ---")
        for symbol in sorted(self.common_symbols)[:limit]:
            values: list[str] = []
            for exchange in EXCHANGES:
                tick = self.latest.get((symbol, exchange))
                values.append(f"{exchange}={tick.last_price if tick else '-'}")

            suffix = ""
            reference = reference_by_symbol.get(symbol)
            mismatch = mismatch_by_symbol.get(symbol)
            if mismatch:
                suffix = (
                    f"  ! {mismatch['status']} spread={mismatch['spread_percent']}%"
                    f" outlier={mismatch['outlier_source']}"
                    f" ref={mismatch['reference_price']}"
                )
            elif symbol in analysis["missing"]:
                suffix = f"  ? missing={','.join(analysis['missing'][symbol])}"
            elif reference:
                suffix = f"  ref={reference['reference_price']}"

            print(f"{symbol}: " + " | ".join(values) + suffix)

        print(f"ticks in memory: {len(self.latest)} / {len(self.common_symbols) * 3}")
        print(
            f"reference-ready: {len(analysis['ready'])} | "
            f"healthy: {len(analysis['healthy'])} | "
            f"degraded: {len(analysis['degraded'])} | "
            f"unresolved: {len(analysis['unresolved'])} | "
            f"insufficient: {len(analysis['insufficient'])} | "
            f"missing: {len(analysis['missing'])}"
        )
        LOGGER.info(
            "health ticks=%s/%s reference_ready=%s healthy=%s degraded=%s "
            "unresolved=%s insufficient=%s missing=%s",
            len(self.latest),
            len(self.common_symbols) * 3,
            len(analysis["ready"]),
            len(analysis["healthy"]),
            len(analysis["degraded"]),
            len(analysis["unresolved"]),
            len(analysis["insufficient"]),
            len(analysis["missing"]),
        )

    async def watch(
        self,
        *,
        seconds: int | None = None,
        print_every: float = 5.0,
    ) -> None:
        if not self.common_symbols:
            await self.discover()

        LOGGER.info(
            "watch started common_symbols=%s seconds=%s print_every=%s",
            len(self.common_symbols),
            seconds,
            print_every,
        )
        stop = asyncio.Event()

        async def reporter() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=print_every)
                except asyncio.TimeoutError:
                    self.print_snapshot()

        tasks = [
            asyncio.create_task(adapter.run(self.common_symbols, self._handle_tick, stop))
            for adapter in self.adapters
        ]
        tasks.append(asyncio.create_task(reporter()))

        try:
            if seconds is None:
                await asyncio.gather(*tasks)
            else:
                await asyncio.sleep(seconds)
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.print_snapshot()
            LOGGER.info("watch stopped")
