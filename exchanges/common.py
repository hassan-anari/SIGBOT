from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


def upper(value: Any) -> str:
    return str(value or "").strip().upper()


def first(row: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def decimal_value(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "result", "list", "rows", "items", "tickers"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def canonical(base: str, quote: str = "USDT") -> str:
    return f"{upper(base)}{upper(quote)}"


def infer_base_from_symbol(symbol: str, quote: str = "USDT") -> str:
    normalized = upper(symbol).replace("-", "").replace("_", "").replace("/", "")
    quote = upper(quote)
    if normalized.endswith(quote) and len(normalized) > len(quote):
        return normalized[: -len(quote)]
    return ""
