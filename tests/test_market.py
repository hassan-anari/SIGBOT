from __future__ import annotations

import unittest
import json
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

from market import MarketProbe
from models import ExchangeStatus, Instrument, PriceTick


class DiscoveryFilterTests(unittest.TestCase):
    def test_numeric_lbank_symbol_is_kept_only_when_common_to_other_two(self) -> None:
        probe = MarketProbe()
        probe.instruments = {
            "ourbit": [
                Instrument("ourbit", "4_USDT", "4USDT", "4"),
                Instrument("ourbit", "BTC_USDT", "BTCUSDT", "BTC"),
            ],
            "toobit": [
                Instrument("toobit", "4-SWAP-USDT", "4USDT", "4"),
                Instrument("toobit", "BTC-SWAP-USDT", "BTCUSDT", "BTC"),
            ],
            "lbank": [
                Instrument("lbank", "4USDT", "4USDT", "4"),
                Instrument("lbank", "1451800USDT", "1451800USDT", "1451800"),
                Instrument("lbank", "BTCUSDT", "BTCUSDT", "BTC"),
            ],
        }
        probe.lbank.numeric_only_candidates = ["1451800USDT", "4USDT"]

        probe._filter_lbank_numeric_contracts()

        self.assertEqual(
            {item.canonical_symbol for item in probe.instruments["lbank"]},
            {"4USDT", "BTCUSDT"},
        )
        self.assertEqual(probe.lbank.preserved_numeric_symbols, ["4USDT"])
        self.assertEqual(probe.lbank.ignored_numeric_symbols, ["1451800USDT"])


class PriceStatusTests(unittest.TestCase):
    @staticmethod
    def add_price(
        probe: MarketProbe,
        symbol: str,
        exchange: str,
        price: str,
    ) -> None:
        probe.latest[(symbol, exchange)] = PriceTick.now(
            exchange=exchange,
            exchange_symbol=symbol,
            canonical_symbol=symbol,
            last_price=Decimal(price),
            source="rest",
        )

    def test_healthy_degraded_and_insufficient(self) -> None:
        probe = MarketProbe(max_spread_percent=Decimal("5"))
        probe.common_symbols = {"BTCUSDT", "1000XECUSDT", "ETHUSDT"}

        for exchange, price in {
            "ourbit": "100",
            "toobit": "101",
            "lbank": "100.5",
        }.items():
            self.add_price(probe, "BTCUSDT", exchange, price)

        for exchange, price in {
            "ourbit": "0.0061",
            "toobit": "0.00609",
            "lbank": "0.03442",
        }.items():
            self.add_price(probe, "1000XECUSDT", exchange, price)

        self.add_price(probe, "ETHUSDT", "ourbit", "3000")

        analysis = probe.analyze_prices()

        self.assertEqual(analysis["ready"], ["1000XECUSDT", "BTCUSDT"])
        self.assertEqual(analysis["healthy"], ["BTCUSDT"])
        self.assertEqual(analysis["degraded"][0]["symbol"], "1000XECUSDT")
        self.assertEqual(analysis["degraded"][0]["outlier_source"], "lbank")
        self.assertEqual(analysis["mismatches"][0]["reference_price"], "0.0061")
        self.assertEqual(analysis["missing"]["ETHUSDT"], ["toobit", "lbank"])
        self.assertEqual(analysis["insufficient"], ["ETHUSDT"])

    def test_two_close_sources_use_average(self) -> None:
        probe = MarketProbe(max_spread_percent=Decimal("5"))
        probe.common_symbols = {"SOLUSDT"}
        self.add_price(probe, "SOLUSDT", "ourbit", "100")
        self.add_price(probe, "SOLUSDT", "toobit", "102")

        analysis = probe.analyze_prices()
        self.assertEqual(analysis["ready"], ["SOLUSDT"])
        self.assertEqual(analysis["references"][0]["method"], "average_2")
        self.assertEqual(analysis["references"][0]["reference_price"], "101")
        self.assertEqual(analysis["missing"]["SOLUSDT"], ["lbank"])

    def test_three_disagreeing_sources_are_unresolved(self) -> None:
        probe = MarketProbe(max_spread_percent=Decimal("5"))
        probe.common_symbols = {"TESTUSDT"}
        self.add_price(probe, "TESTUSDT", "ourbit", "100")
        self.add_price(probe, "TESTUSDT", "toobit", "120")
        self.add_price(probe, "TESTUSDT", "lbank", "150")

        analysis = probe.analyze_prices()
        self.assertEqual(analysis["ready"], [])
        self.assertEqual(analysis["unresolved"], ["TESTUSDT"])
        self.assertEqual(analysis["mismatches"][0]["reason"], "no_agreeing_pair")


class FreshnessTests(unittest.TestCase):
    NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

    @staticmethod
    def tick(symbol: str, exchange: str, price: str, received_at: datetime) -> PriceTick:
        return PriceTick(exchange, symbol, symbol, Decimal(price), received_at, "rest")

    def probe(self) -> MarketProbe:
        probe = MarketProbe(clock=lambda: self.NOW)
        probe.common_symbols = {"BTCUSDT"}
        return probe

    def test_three_fresh_prices_are_healthy(self) -> None:
        probe = self.probe()
        for exchange, price in zip(("ourbit", "toobit", "lbank"), ("100", "101", "102")):
            probe.latest[("BTCUSDT", exchange)] = self.tick("BTCUSDT", exchange, price, self.NOW)
        analysis = probe.analyze_prices(self.NOW)
        self.assertEqual(analysis["healthy"], ["BTCUSDT"])
        self.assertEqual(analysis["references"][0]["sources"], ["lbank", "ourbit", "toobit"])

    def test_stale_and_missing_are_separate_and_stale_is_not_a_source(self) -> None:
        probe = self.probe()
        probe.latest[("BTCUSDT", "ourbit")] = self.tick("BTCUSDT", "ourbit", "100", self.NOW)
        probe.latest[("BTCUSDT", "toobit")] = self.tick("BTCUSDT", "toobit", "102", self.NOW)
        probe.latest[("BTCUSDT", "lbank")] = self.tick("BTCUSDT", "lbank", "500", self.NOW - timedelta(seconds=16))
        analysis = probe.analyze_prices(self.NOW)
        self.assertEqual(analysis["degraded"][0]["stale"], ["lbank"])
        self.assertNotIn("BTCUSDT", analysis["missing"])
        self.assertEqual(analysis["references"][0]["sources"], ["ourbit", "toobit"])
        self.assertEqual(analysis["mismatches"], [])

    def test_one_fresh_with_stale_and_missing_is_insufficient(self) -> None:
        probe = self.probe()
        probe.latest[("BTCUSDT", "ourbit")] = self.tick("BTCUSDT", "ourbit", "100", self.NOW)
        probe.latest[("BTCUSDT", "toobit")] = self.tick("BTCUSDT", "toobit", "101", self.NOW - timedelta(seconds=16))
        analysis = probe.analyze_prices(self.NOW)
        self.assertEqual(analysis["insufficient"], ["BTCUSDT"])
        self.assertEqual(analysis["missing"]["BTCUSDT"], ["lbank"])
        self.assertEqual(analysis["stale"]["BTCUSDT"][0]["exchange"], "toobit")

    def test_all_stored_prices_stale_produce_no_reference(self) -> None:
        probe = self.probe()
        for exchange in ("ourbit", "toobit", "lbank"):
            probe.latest[("BTCUSDT", exchange)] = self.tick("BTCUSDT", exchange, "100", self.NOW - timedelta(seconds=16))
        analysis = probe.analyze_prices(self.NOW)
        self.assertEqual(analysis["ready"], [])
        self.assertEqual(analysis["references"], [])

    def test_fifteen_second_boundary_is_fresh_but_beyond_is_stale(self) -> None:
        probe = self.probe()
        probe.latest[("BTCUSDT", "ourbit")] = self.tick("BTCUSDT", "ourbit", "100", self.NOW - timedelta(seconds=15))
        probe.latest[("BTCUSDT", "toobit")] = self.tick("BTCUSDT", "toobit", "100", self.NOW - timedelta(seconds=15, microseconds=1))
        analysis = probe.analyze_prices(self.NOW)
        self.assertNotIn("ourbit", [row["exchange"] for row in analysis["stale"]["BTCUSDT"]])
        self.assertEqual(analysis["stale"]["BTCUSDT"][0]["exchange"], "toobit")

    def test_naive_analysis_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.probe().analyze_prices(datetime(2026, 1, 1))


class SnapshotHealthTests(unittest.IsolatedAsyncioTestCase):
    NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.probe = MarketProbe(clock=lambda: self.NOW)
        self.probe.common_symbols = {"BTCUSDT", "ETHUSDT"}
        self.probe.instruments = {
            exchange: [
                Instrument(exchange, f"BTC-{exchange}", "BTCUSDT", "BTC"),
                Instrument(exchange, f"ETH-{exchange}", "ETHUSDT", "ETH"),
            ] for exchange in ("ourbit", "toobit", "lbank")
        }

    def tick(self, symbol: str, exchange: str, at: datetime | None = None) -> PriceTick:
        return PriceTick(exchange, symbol, symbol, Decimal("100"), at or self.NOW, "rest")

    async def test_complete_partial_and_recovery(self) -> None:
        self.assertEqual(self.probe.health["ourbit"].status, ExchangeStatus.UNKNOWN)
        await self.probe._handle_snapshot("ourbit", [self.tick("BTCUSDT", "ourbit"), self.tick("ETHUSDT", "ourbit")], self.NOW)
        self.assertEqual(self.probe.health["ourbit"].status, ExchangeStatus.HEALTHY)
        later = self.NOW + timedelta(seconds=5)
        await self.probe._handle_snapshot("ourbit", [self.tick("BTCUSDT", "ourbit", later)], later)
        state = self.probe.health["ourbit"]
        self.assertEqual(state.status, ExchangeStatus.DEGRADED)
        self.assertEqual((state.expected_count, state.received_count, state.missing_count), (2, 1, 1))
        self.assertIn(("ETHUSDT", "ourbit"), self.probe.latest)
        expired = self.probe.analyze_prices(self.NOW + timedelta(seconds=16))
        self.assertIn("ourbit", [row["exchange"] for row in expired["stale"]["ETHUSDT"]])
        recovered = later + timedelta(seconds=10)
        await self.probe._handle_snapshot("ourbit", [self.tick("BTCUSDT", "ourbit", recovered), self.tick("ETHUSDT", "ourbit", recovered)], recovered)
        self.assertEqual(self.probe.health["ourbit"].status, ExchangeStatus.HEALTHY)
        self.assertIsNone(self.probe.health["ourbit"].unhealthy_since)

    async def test_failure_keeps_ticks_and_complete_response_recovers(self) -> None:
        await self.probe._handle_snapshot("ourbit", [self.tick("BTCUSDT", "ourbit"), self.tick("ETHUSDT", "ourbit")], self.NOW)
        failed = self.NOW + timedelta(seconds=5)
        await self.probe._handle_failure("ourbit", RuntimeError("temporary"), failed)
        self.assertEqual(self.probe.health["ourbit"].status, ExchangeStatus.DOWN)
        self.assertEqual(len(self.probe.latest), 2)
        recovered = failed + timedelta(seconds=7)
        transition = self.probe._set_health("ourbit", ExchangeStatus.HEALTHY, recovered, expected_count=2, received_count=2, missing_count=0)
        self.assertEqual(transition.unhealthy_duration, timedelta(seconds=7))
        self.assertIsNone(self.probe.health["ourbit"].last_error)

    async def test_repeated_status_has_no_transition(self) -> None:
        first = self.probe._set_health("ourbit", ExchangeStatus.DEGRADED, self.NOW, expected_count=2, received_count=1, missing_count=1)
        repeated = self.probe._set_health("ourbit", ExchangeStatus.DEGRADED, self.NOW + timedelta(seconds=5), expected_count=2, received_count=1, missing_count=1)
        self.assertIsNotNone(first)
        self.assertIsNone(repeated)

    async def test_degraded_down_and_down_degraded_keep_unhealthy_start(self) -> None:
        self.probe._set_health("ourbit", ExchangeStatus.DEGRADED, self.NOW, expected_count=2, received_count=1, missing_count=1)
        self.probe._set_health("ourbit", ExchangeStatus.DOWN, self.NOW + timedelta(seconds=5), expected_count=2, received_count=0, missing_count=2, error="failed")
        self.probe._set_health("ourbit", ExchangeStatus.DEGRADED, self.NOW + timedelta(seconds=10), expected_count=2, received_count=1, missing_count=1)
        self.assertEqual(self.probe.health["ourbit"].unhealthy_since, self.NOW)

    async def test_fetch_once_failure_marks_down_and_raises(self) -> None:
        self.probe.ourbit.fetch_prices = AsyncMock(side_effect=RuntimeError("failed"))
        self.probe.toobit.fetch_prices = AsyncMock(return_value=[self.tick("BTCUSDT", "toobit"), self.tick("ETHUSDT", "toobit")])
        self.probe.lbank.fetch_prices = AsyncMock(return_value=[self.tick("BTCUSDT", "lbank"), self.tick("ETHUSDT", "lbank")])
        with self.assertRaises(RuntimeError):
            await self.probe.fetch_once()
        self.assertEqual(self.probe.health["ourbit"].status, ExchangeStatus.DOWN)


class CollisionAndExportTests(unittest.TestCase):
    def test_collisions_are_order_independent_and_ambiguous_is_excluded(self) -> None:
        rows = [
            Instrument("ourbit", "BTC_USDT", "BTCUSDT", "BTC"),
            Instrument("ourbit", "BTC-USDT", "BTCUSDT", "BTC"),
            Instrument("ourbit", "ETH_USDT", "ETHUSDT", "ETH"),
            Instrument("ourbit", "ETH_USDT", "ETHUSDT", "ETH"),
        ]
        results = []
        for ordered in (rows, list(reversed(rows))):
            probe = MarketProbe()
            probe.instruments = {"ourbit": ordered, "toobit": [], "lbank": []}
            probe._exclude_canonical_collisions()
            results.append((probe.collisions["ourbit"], [i.canonical_symbol for i in probe.instruments["ourbit"]]))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0]["BTCUSDT"], ["BTC-USDT", "BTC_USDT"])
        self.assertEqual(results[0][1], ["ETHUSDT"])

    def test_exports_existing_and_new_diagnostics(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        probe = MarketProbe(clock=lambda: now)
        probe.common_symbols = {"BTCUSDT"}
        probe.instruments = {exchange: [Instrument(exchange, "BTC", "BTCUSDT", "BTC")] for exchange in ("ourbit", "toobit", "lbank")}
        probe.latest[("BTCUSDT", "ourbit")] = PriceTick("ourbit", "BTC", "BTCUSDT", Decimal("100"), now - timedelta(seconds=16), "rest")
        probe.collisions["toobit"] = {"BADUSDT": ["BAD-1", "BAD-2"]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            probe.export_symbols(output)
            expected = {"common_symbols.json", "symbol_mapping.json", "symbol_diagnostics.json", "price_ready_symbols.json", "healthy_symbols.json", "degraded_symbols.json", "reference_prices.json", "price_mismatches.json", "missing_prices.json", "lbank_ignored_numeric_symbols.json", "stale_prices.json", "symbol_collisions.json"}
            self.assertTrue(expected <= {path.name for path in output.iterdir()})
            diagnostics = json.loads((output / "symbol_diagnostics.json").read_text())
            self.assertEqual(diagnostics["stale_price_count"], 1)
            self.assertEqual(diagnostics["collision_count"], 1)
            self.assertIn("common_count", diagnostics)


if __name__ == "__main__":
    unittest.main()
