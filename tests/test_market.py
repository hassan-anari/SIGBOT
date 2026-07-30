from __future__ import annotations

import unittest
from decimal import Decimal

from market import MarketProbe
from models import Instrument, PriceTick


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


if __name__ == "__main__":
    unittest.main()
