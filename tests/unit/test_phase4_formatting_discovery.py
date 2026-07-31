import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from chainura.domain.destination import DestinationType
from chainura.telegram.destination_inspector import (DestinationInspectionError,
    InspectionError, TelegramDestinationInspector, normalize_chat_id)
from chainura.telegram.presentation.formatting import (format_datetime,
    format_priority, gregorian_to_jalali)


class FormattingTests(unittest.TestCase):
    def test_known_jalali_dates(self):
        self.assertEqual(gregorian_to_jalali(2026, 7, 31), (1405, 5, 9))
        self.assertEqual(gregorian_to_jalali(2024, 3, 20), (1403, 1, 1))

    def test_tehran_midnight_digits_and_precision(self):
        result = format_datetime(datetime(2026, 7, 30, 21, 30, 59, 123, timezone.utc))
        self.assertEqual(result, "۱۴۰۵/۰۵/۰۹، ساعت ۰۱:۰۰")
        self.assertNotIn("59", result); self.assertNotIn("+", result)

    def test_none_and_naive(self):
        self.assertEqual(format_datetime(None), "ثبت نشده")
        with self.assertRaises(ValueError): format_datetime(datetime(2026, 1, 1))

    def test_vertical_branded_priority(self):
        self.assertEqual(format_priority(["lbank", "ourbit"]),
                         "🏦 اولویت صرافی‌ها:\n۱. LBank\n۲. Ourbit")


class NormalizationTests(unittest.TestCase):
    def test_accepted_forms(self):
        for raw in ("3147975662", "1003147975662", " -1003147975662 "):
            self.assertEqual(normalize_chat_id(raw), -1003147975662)

    def test_invalid_forms(self):
        for raw in ("", "0", "abc", "1.2", "-3147975662", str(2**63)):
            with self.subTest(raw=raw), self.assertRaises(ValueError): normalize_chat_id(raw)


class FakeBot:
    def __init__(self, chat_type="channel", status="administrator"):
        self.chat_type=chat_type; self.status=status; self.calls=[]
    async def get_me(self): self.calls.append("me"); return SimpleNamespace(id=9)
    async def get_chat(self, chat_id):
        self.calls.append("chat"); return SimpleNamespace(type=self.chat_type,title="<مقصد>")
    async def get_chat_member(self, chat_id, user_id):
        self.calls.append("member"); return SimpleNamespace(status=self.status)


class InspectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_supported_types_and_admin_statuses(self):
        for kind, expected in (("channel",DestinationType.CHANNEL),("group",DestinationType.GROUP),("supergroup",DestinationType.GROUP)):
            for status in ("creator","administrator"):
                bot=FakeBot(kind,status); result=await TelegramDestinationInspector(bot).inspect(-1001)
                self.assertEqual(result.destination_type,expected); self.assertTrue(result.bot_is_admin)
                self.assertEqual(bot.calls,["me","chat","member"])
    async def test_non_admin_rejected_by_result_without_sending(self):
        for status in ("member","restricted","left","kicked"):
            bot=FakeBot(status=status); result=await TelegramDestinationInspector(bot).inspect(-1001)
            self.assertFalse(result.bot_is_admin); self.assertFalse(hasattr(bot,"send_message"))
    async def test_private_is_sanitized(self):
        with self.assertRaises(DestinationInspectionError) as caught:
            await TelegramDestinationInspector(FakeBot("private")).inspect(-1001)
        self.assertEqual(caught.exception.reason,InspectionError.PRIVATE)
