from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
NOT_RECORDED = "ثبت نشده"


def persian_digits(value: object) -> str:
    return str(value).translate(PERSIAN_DIGITS)


def gregorian_to_jalali(year: int, month: int, day: int) -> tuple[int, int, int]:
    days = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy = year - 1600
    gm = month - 1
    gd = day - 1
    total = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    total += days[gm] + gd
    if gm > 1 and ((year % 4 == 0 and year % 100 != 0) or year % 400 == 0):
        total += 1
    total -= 79
    cycles, total = divmod(total, 12053)
    jy = 979 + 33 * cycles + 4 * (total // 1461)
    total %= 1461
    if total >= 366:
        jy += (total - 1) // 365
        total = (total - 1) % 365
    if total < 186:
        jm, jd = 1 + total // 31, 1 + total % 31
    else:
        jm, jd = 7 + (total - 186) // 30, 1 + (total - 186) % 30
    return jy, jm, jd


def format_datetime(value: datetime | None) -> str:
    if value is None:
        return NOT_RECORDED
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    local = value.astimezone(TEHRAN)
    jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
    return persian_digits(f"{jy:04d}/{jm:02d}/{jd:02d}، ساعت {local.hour:02d}:{local.minute:02d}")


EXCHANGE_NAMES = {"ourbit": "Ourbit", "toobit": "Toobit", "lbank": "LBank"}


def format_priority(values) -> str:
    rows = [f"{persian_digits(index)}. {EXCHANGE_NAMES[getattr(value, 'value', value)]}"
            for index, value in enumerate(values, 1)]
    return "🏦 اولویت صرافی‌ها:\n" + ("\n".join(rows) if rows else NOT_RECORDED)
