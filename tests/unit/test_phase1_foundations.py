from decimal import Decimal
import unittest

from aiogram.enums import ParseMode

from chainura.application.signal_service import SignalService
from chainura.domain.monitoring import SignalEventKind
from chainura.domain.signal import parse_leverage
from chainura.errors import InvalidSignalInputError, SignalCreationError
from chainura.application.signalist_notification import event_text, prompt_text
from chainura.telegram.active_signal_routes import detail_text, _label
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import signal_review_actions, signal_stage_one_actions
from chainura.telegram.runtime import production_bot_defaults
from chainura.trade_id import format_trade_id


class TradeIdTests(unittest.TestCase):
    def test_format_and_growth(self):
        self.assertEqual(format_trade_id(1), "TR-000001")
        self.assertEqual(format_trade_id(42), "TR-000042")
        self.assertEqual(format_trade_id(123), "TR-000123")
        self.assertEqual(format_trade_id(1_000_000), "TR-1000000")

    def test_invalid_ids_fail_clearly(self):
        for value in (0, -1, True, "1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                format_trade_id(value)

    def test_event_and_prompt_include_trade_id(self):
        for kind in (SignalEventKind.TP_HIT, SignalEventKind.SL_HIT,
                     SignalEventKind.BREAKEVEN):
            text = event_text(kind, "BTCUSDT", Decimal("100"), Decimal("1"),
                              1 if kind is SignalEventKind.TP_HIT else None, 42)
            self.assertIn("TR-000042", text)
        self.assertIn("TR-000042", prompt_text(
            "BTCUSDT", Decimal("100"), Decimal("100.1"), 42))

    def test_active_and_report_present_public_id(self):
        item = type("Item", (), {"id": 42, "symbol": "BTCUSDT",
            "direction": __import__("chainura.domain.signal", fromlist=["TradeDirection"]).TradeDirection.LONG,
            "status": __import__("chainura.domain.signal", fromlist=["SignalLifecycleStatus"]).SignalLifecycleStatus.PENDING_DETAILS,
            "entry": Decimal("100"), "leverage": 10, "take_profits": (),
            "stop_loss": None})()
        self.assertIn("TR-000042", detail_text(item))
        self.assertIn("TR-000042", _label(item))


class LeveragePolicyTests(unittest.TestCase):
    def test_parser_accepts_boundaries_and_rejects_invalid(self):
        self.assertEqual(parse_leverage("1"), 1)
        self.assertEqual(parse_leverage("50"), 50)
        for value in ("0", "51", "1.5", "abc", "۵"):
            with self.subTest(value=value), self.assertRaises(InvalidSignalInputError):
                parse_leverage(value)

    def test_service_creation_policy_is_authoritative(self):
        with self.assertRaises(SignalCreationError):
            SignalService._validate_new_leverage(51)
        SignalService._validate_new_leverage(50)


class TelegramFoundationContractTests(unittest.TestCase):
    def test_global_bot_defaults_keep_html_and_disable_previews(self):
        defaults = production_bot_defaults()
        self.assertEqual(defaults.parse_mode, ParseMode.HTML)
        self.assertTrue(defaults.link_preview_is_disabled)

    def test_stage_keyboards_expose_required_actions(self):
        stage_one = [button.text for row in signal_stage_one_actions("token").inline_keyboard
                     for button in row]
        self.assertEqual(stage_one, [texts.SIGNAL_STAGE_ONE_CONFIRM,
                                     texts.SIGNAL_STAGE_ONE_EDIT,
                                     texts.SIGNAL_CANCEL])
        stage_two = [button.text for row in signal_review_actions("token").inline_keyboard
                     for button in row]
        self.assertIn(texts.SIGNAL_EDIT_DETAILS, stage_two)

    def test_stage_one_review_has_all_creation_fields(self):
        rendered = texts.signal_stage_one_review({"canonical_symbol": "BTCUSDT",
            "mode": "single_exchange", "selected_exchange": "ourbit",
            "direction": "long", "p0_reference_price": "123.45", "leverage": 50})
        for expected in ("BTCUSDT", "Ourbit", "123.45", "50"):
            self.assertIn(expected, rendered)

    def test_raw_urls_are_not_rewritten(self):
        value = "https://example.com/path"
        self.assertEqual(texts.safe(value), value)


if __name__ == "__main__":
    unittest.main()
