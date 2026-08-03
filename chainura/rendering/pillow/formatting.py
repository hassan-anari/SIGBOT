from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo


def format_decimal(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("value must be a finite Decimal")
    return format(value, "f")


def format_entry_price(value: Decimal) -> str: return format_decimal(value)
def format_display_price(value: Decimal) -> str: return format_decimal(value)
def format_initial_pnl(value: Decimal) -> str:
    if value != Decimal("0"):
        raise ValueError("initial PnL must be zero")
    return "0.00%"
def format_leverage(value: int) -> str: return f"{value}X"
def format_elapsed(seconds: int) -> str:
    if seconds < 0: raise ValueError("elapsed duration cannot be negative")
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours:02d}H {minutes:02d}M"
def format_tehran_timestamp(value: datetime) -> str:
    return value.astimezone(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M:%S")
def format_utc_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
def format_symbol(value: str) -> str:
    if not value or not value.isascii(): raise ValueError("symbol must be non-empty ASCII")
    return value.upper()
