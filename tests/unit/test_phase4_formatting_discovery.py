import os
import subprocess
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.methods import GetMe

from chainura.domain.destination import DestinationType
from chainura.telegram.destination_inspector import (DestinationInspectionError,
    InspectionError, TelegramDestinationInspector, normalize_chat_id)
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.formatting import (format_datetime,
    format_priority, gregorian_to_jalali)


class FormattingTests(unittest.TestCase):
    def test_known_jalali_dates(self):
        self.assertEqual(gregorian_to_jalali(2026, 7, 31), (1405, 5, 9))
        self.assertEqual(gregorian_to_jalali(2024, 3, 20), (1403, 1, 1))

    def test_tehran_midnight_digits_and_precision(self):
        result = format_datetime(datetime(2026, 7, 30, 21, 30, 59, 123, timezone.utc),
                                 "{date}، ساعت {time}")
        self.assertEqual(result, "۱۴۰۵/۰۵/۰۹، ساعت ۰۱:۰۰")
        self.assertNotIn("59", result); self.assertNotIn("+", result)

    def test_none_and_naive(self):
        self.assertIsNone(format_datetime(None, "{date} {time}"))
        self.assertEqual(texts.datetime_value(None), "ثبت نشده")
        with self.assertRaises(ValueError): format_datetime(datetime(2026, 1, 1), "{date} {time}")
        formatting_source=(Path(__file__).parents[2] / "chainura" / "telegram" /
                           "presentation" / "formatting.py").read_text(encoding="utf-8")
        self.assertNotIn("ثبت نشده",formatting_source)

    def test_tzdata_fallback_when_system_database_is_disabled(self):
        environment = dict(os.environ, PYTHONTZPATH="")
        code = ("from datetime import datetime,timezone; "
                "from chainura.telegram.presentation.formatting import TEHRAN,format_datetime; "
                "assert TEHRAN.key == 'Asia/Tehran'; "
                "assert format_datetime(datetime(2026,7,30,21,30,tzinfo=timezone.utc),"
                "'{date}، ساعت {time}') == "
                "'۱۴۰۵/۰۵/۰۹، ساعت ۰۱:۰۰'")
        result = subprocess.run([sys.executable, "-c", code], env=environment,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_vertical_branded_priority(self):
        self.assertEqual(format_priority(["lbank", "ourbit"], "🏦 اولویت صرافی‌ها:", "ثبت نشده"),
                         "🏦 اولویت صرافی‌ها:\n۱. LBank\n۲. Ourbit")


class NormalizationTests(unittest.TestCase):
    def test_accepted_forms(self):
        for raw in ("3147975662", "1003147975662", " -1003147975662 "):
            self.assertEqual(normalize_chat_id(raw), -1003147975662)
        self.assertEqual(normalize_chat_id("-123456789"), -123456789)

    def test_invalid_forms(self):
        for raw in ("", "0", "abc", "1.2", "-0123", "--123", "−123", str(2**63)):
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

    async def test_telegram_exceptions_are_sanitized(self):
        for error,reason in ((TelegramBadRequest(GetMe(),"secret payload"),InspectionError.NOT_FOUND),
                             (TelegramNetworkError(GetMe(),"secret payload"),InspectionError.TEMPORARY),
                             (TelegramServerError(GetMe(),"secret payload"),InspectionError.TEMPORARY)):
            bot=SimpleNamespace(get_me=AsyncMock(side_effect=error))
            with self.subTest(error=type(error).__name__), self.assertRaises(DestinationInspectionError) as caught:
                await TelegramDestinationInspector(bot).inspect(-1001)
            self.assertEqual(caught.exception.reason,reason)
            self.assertNotIn("secret",str(caught.exception))
