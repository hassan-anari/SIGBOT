from __future__ import annotations

import json
import unittest
from pathlib import Path

from exchanges.lbank import LBankAdapter
from exchanges.ourbit import OurbitAdapter
from exchanges.toobit import ToobitAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class AdapterParsingTests(unittest.TestCase):
    def test_ourbit_instruments_and_prices(self) -> None:
        payload = load("ourbit_ticker.json")
        instruments = OurbitAdapter.parse_instruments(payload)
        self.assertEqual(
            {item.canonical_symbol for item in instruments},
            {"BTCUSDT", "ETHUSDT"},
        )
        symbol_map = {
            item.exchange_symbol: item.canonical_symbol for item in instruments
        }
        ticks = OurbitAdapter.parse_prices(payload, symbol_map)
        self.assertEqual(str(ticks[0].last_price), "67240.5")

    def test_lbank_keeps_numeric_candidates_for_cross_exchange_filter(self) -> None:
        payload = load("lbank_instruments.json")
        instruments = LBankAdapter.parse_instruments(payload)
        self.assertEqual(
            {item.canonical_symbol for item in instruments},
            {"BTCUSDT", "ETHUSDT", "1451800USDT", "4USDT", "1000XECUSDT"},
        )
        self.assertEqual(
            LBankAdapter.numeric_only_symbols(payload),
            ["1451800USDT", "4USDT"],
        )

    def test_lbank_prices(self) -> None:
        instruments = LBankAdapter.parse_instruments(load("lbank_instruments.json"))
        symbol_map = {
            item.exchange_symbol: item.canonical_symbol for item in instruments
        }
        ticks = LBankAdapter.parse_prices(load("lbank_prices.json"), symbol_map)
        self.assertEqual(
            {tick.canonical_symbol for tick in ticks},
            {"BTCUSDT", "ETHUSDT"},
        )

    def test_toobit_filters_inverse_contract(self) -> None:
        instruments = ToobitAdapter.parse_instruments(
            load("toobit_exchange_info.json")
        )
        self.assertEqual(len(instruments), 1)
        self.assertEqual(instruments[0].canonical_symbol, "BTCUSDT")

    def test_toobit_rest_ticker(self) -> None:
        ticks = ToobitAdapter.parse_prices(
            load("toobit_prices.json"),
            {
                "BTC-SWAP-USDT": "BTCUSDT",
                "ETH-SWAP-USDT": "ETHUSDT",
            },
        )
        self.assertEqual(len(ticks), 2)
        self.assertEqual(str(ticks[0].last_price), "67243.7")
        self.assertTrue(all(tick.source == "rest" for tick in ticks))


if __name__ == "__main__":
    unittest.main()
