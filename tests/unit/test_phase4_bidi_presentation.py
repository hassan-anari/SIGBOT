import re
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from chainura.domain.destination import (
    Destination,
    DestinationAccessLevel,
    DestinationStatus,
    DestinationType,
    Exchange,
)
from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.formatting import LRI, PDI, RLM, bidi_ascii_id


ID_ON_LABEL_LINE = re.compile(r"🆔[^\n]*:\s*-?[0-9]+")
PERSIAN_DIGITS = set("۰۱۲۳۴۵۶۷۸۹")


def semantic_id(rendered: str) -> str:
    return rendered.removeprefix("<code>").removesuffix("</code>").replace(LRI, "").replace(PDI, "")


class BidiAsciiIdTests(unittest.TestCase):
    def test_supported_id_values_are_isolated_ascii_code(self):
        values = (123456789, "123456789", -1003147975662, -987654321,
                  -(2**63), 2**63 - 1)
        for value in values:
            with self.subTest(value=value):
                rendered = bidi_ascii_id(value)
                expected = str(value)
                self.assertEqual(rendered, f"<code>{LRI}{expected}{PDI}</code>")
                self.assertEqual(semantic_id(rendered), expected)
                self.assertTrue(semantic_id(rendered).isascii())
                self.assertFalse(PERSIAN_DIGITS.intersection(rendered))
                if expected.startswith("-"):
                    self.assertEqual(semantic_id(rendered).index("-"), 0)

    def test_invalid_or_non_ascii_dynamic_values_are_rejected(self):
        for value in (True, None, "۱۲۳", "+123", "01", "-0", "1<script>",
                      str(2**63), str(-(2**63) - 1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bidi_ascii_id(value)  # type: ignore[arg-type]

    def test_validated_dynamic_value_is_html_escaped_before_insertion(self):
        with patch("chainura.telegram.presentation.formatting.escape", wraps=lambda value, quote: value) as escaped:
            self.assertEqual(semantic_id(bidi_ascii_id("-100123")), "-100123")
        escaped.assert_called_once_with("-100123", quote=True)


class Phase4ScreenRenderingTests(unittest.TestCase):
    def setUp(self):
        now = datetime(2026, 7, 31, tzinfo=timezone.utc)
        self.signalist = Signalist.rehydrate(
            id=918273,
            telegram_user_id=123456789,
            display_name="رضا - اوربیت",
            telegram_username=None,
            status=SignalistStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        destination = Destination.rehydrate(
            id=564738,
            internal_name="Chainura | Test",
            telegram_chat_id=-1003147975662,
            destination_type=DestinationType.CHANNEL,
            access_level=DestinationAccessLevel.PRIVATE,
            status=DestinationStatus.INACTIVE,
            daily_limit=10,
            created_at=now,
            updated_at=now,
        )
        self.detail = SimpleNamespace(
            destination=destination,
            priority=SimpleNamespace(exchanges=(Exchange.OURBIT, Exchange.TOOBIT)),
            assigned_count=2,
        )
        self.signalist_data = {
            "telegram_user_id": 123456789,
            "display_name": "رضا - اوربیت",
        }
        self.destination_data = {
            "internal_name": "Chainura | Test",
            "telegram_chat_id": -1003147975662,
            "destination_type": "channel",
            "access_level": "private",
            "daily_limit": 10,
            "exchanges": ["ourbit", "toobit"],
        }

    def assert_id_block(self, rendered: str, label: str, identifier: str, internal_id: int):
        block = f"{RLM}🆔 {label}:\n<code>{LRI}{identifier}{PDI}</code>"
        self.assertIn(block, rendered)
        self.assertNotRegex(rendered, ID_ON_LABEL_LINE)
        self.assertNotIn(f"{identifier.lstrip('-')}-", rendered)
        self.assertNotIn(str(internal_id), rendered)

    def test_signalist_creation_review_structure(self):
        rendered = texts.signalist_review(self.signalist_data)
        self.assertEqual(
            rendered,
            f"<b>👤 بررسی اطلاعات سیگنالیست</b>\n\n"
            f"{RLM}🆔 شناسه تلگرام:\n<code>{LRI}123456789{PDI}</code>\n\n"
            "📝 نام نمایشی: رضا - اوربیت\n\nآیا اطلاعات بالا تأیید می‌شود؟",
        )
        self.assert_id_block(rendered, "شناسه تلگرام", "123456789", 918273)

    def test_signalist_detail_structure(self):
        rendered = texts.signalist_detail(self.signalist, 2)
        self.assert_id_block(rendered, "شناسه تلگرام", "123456789", 918273)

    def test_destination_candidate_confirmation_structure(self):
        rendered = texts.candidate_review(self.destination_data)
        self.assertIn("<b>✅ مقصد پیدا شد</b>", rendered)
        self.assert_id_block(rendered, "شناسه چت", "-1003147975662", 564738)

    def test_destination_final_review_structure(self):
        rendered = texts.destination_review(self.destination_data)
        self.assert_id_block(rendered, "شناسه چت", "-1003147975662", 564738)

    def test_destination_detail_structure(self):
        rendered = texts.destination_detail(self.detail)
        self.assert_id_block(rendered, "شناسه چت", "-1003147975662", 564738)


class Phase4MessageCatalogTests(unittest.TestCase):
    def test_discovery_error_messages_are_sanitized_and_distinct(self):
        self.assertIn("مقصد پیدا نشد", texts.DESTINATION_NOT_FOUND)
        self.assertIn("ربات عضو کانال یا گروه باشد", texts.DESTINATION_NOT_FOUND)
        self.assertIn("دسترسی مدیر داشته باشد", texts.DESTINATION_NOT_FOUND)
        self.assertIn("ربات در این مقصد مدیر نیست", texts.DESTINATION_NOT_ADMIN)
        self.assertIn("ارتباط با تلگرام موقتاً برقرار نشد", texts.TELEGRAM_TEMPORARY)
        self.assertEqual(len({texts.DESTINATION_NOT_FOUND, texts.DESTINATION_NOT_ADMIN,
                              texts.TELEGRAM_TEMPORARY}), 3)
        for message in (texts.DESTINATION_NOT_FOUND, texts.DESTINATION_NOT_ADMIN,
                        texts.TELEGRAM_TEMPORARY):
            self.assertNotIn("secret payload", message)
            self.assertNotIn("TelegramBadRequest", message)

    def test_identifier_prompts_are_explicitly_rtl_and_persian(self):
        self.assertEqual(texts.ASK_SIGNALIST_ID,
                         f"{RLM}🆔 شناسه عددی تلگرام سیگنالیست را وارد کنید.")
        self.assertEqual(texts.ASK_DESTINATION_CHAT_ID,
                         f"{RLM}🆔 شناسه چت کانال یا گروه را وارد کنید.")
        for prompt in (texts.ASK_SIGNALIST_ID, texts.ASK_DESTINATION_CHAT_ID):
            self.assertTrue(prompt.startswith(RLM))
            self.assertRegex(prompt, r"[؀-ۿ]")
