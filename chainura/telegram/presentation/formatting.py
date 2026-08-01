from __future__ import annotations

from datetime import datetime
from html import escape
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _load_tehran_timezone() -> ZoneInfo:
    try:
        return ZoneInfo("Asia/Tehran")
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            "Asia/Tehran timezone data is unavailable; install the declared tzdata dependency"
        ) from exc


TEHRAN = _load_tehran_timezone()
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
RLM = "\u200f"
LRI = "\u2066"
PDI = "\u2069"
_ASCII_NUMERIC_ID = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")
_MIN_SIGNED_BIGINT = -(2**63)
_MAX_SIGNED_BIGINT = 2**63 - 1


def persian_digits(value: object) -> str:
    return str(value).translate(PERSIAN_DIGITS)


def bidi_ascii_id(value: int | str) -> str:
    """Render a validated signed 64-bit ASCII identifier for Telegram HTML."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("identifier must be an integer or ASCII numeric string")
    ascii_value = str(int(value)) if isinstance(value, int) else value
    if not _ASCII_NUMERIC_ID.fullmatch(ascii_value) or not ascii_value.isascii():
        raise ValueError("identifier must be a canonical ASCII numeric value")
    numeric_value = int(ascii_value)
    if ascii_value != str(numeric_value):
        raise ValueError("identifier must be a canonical ASCII numeric value")
    if not _MIN_SIGNED_BIGINT <= numeric_value <= _MAX_SIGNED_BIGINT:
        raise ValueError("identifier must fit in a signed 64-bit integer")
    return f"{LRI}<code>{escape(ascii_value, quote=True)}</code>{PDI}"


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


def format_datetime(value: datetime | None, template: str) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    local = value.astimezone(TEHRAN)
    jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
    return template.format(
        date=persian_digits(f"{jy:04d}/{jm:02d}/{jd:02d}"),
        time=persian_digits(f"{local.hour:02d}:{local.minute:02d}"),
    )


EXCHANGE_NAMES = {"ourbit": "Ourbit", "toobit": "Toobit", "lbank": "LBank"}


def format_priority(values, heading: str, empty_placeholder: str) -> str:
    rows = [f"{persian_digits(index)}. {EXCHANGE_NAMES[getattr(value, 'value', value)]}"
            for index, value in enumerate(values, 1)]
    return heading + "\n" + ("\n".join(rows) if rows else empty_placeholder)
