from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType, TradeDirection
from chainura.rendering.models import (
    SignalRenderEventKind,
    SignalRenderPayload,
)


FIXED_TIME = datetime(
    2026,
    8,
    1,
    12,
    34,
    56,
    tzinfo=timezone.utc,
)

BASE_ENTRY = Decimal("65234.1200")
PREVIEW_PNL_MAGNITUDE = Decimal("12.34")


def _payload(
    *,
    direction: TradeDirection = TradeDirection.LONG,
    symbol: str = "BTCUSDT",
    entry: Decimal = BASE_ENTRY,
    display: Decimal | None = None,
    leverage: int = 10,
    source: PriceSourceType = PriceSourceType.COMPOSITE,
) -> SignalRenderPayload:
    """
    Build a production-valid INITIAL render payload.

    initial_pnl_percent intentionally remains Decimal zero because the
    production rendering contract allows only zero PnL for initial cards.
    Renderer-Lab-only visual PnL values are supplied separately.
    """
    is_long = direction is TradeDirection.LONG
    step = entry / Decimal("10")
    display_price = entry if display is None else display

    take_profits = (
        (entry + step, entry + step * 2)
        if is_long
        else (entry - step, entry - step * 2)
    )

    stop_loss = entry - step if is_long else entry + step

    return SignalRenderPayload(
        signal_id=501,
        event_kind=SignalRenderEventKind.INITIAL,
        exchange=Exchange.OURBIT,
        direction=direction,
        price_source_type=source,
        canonical_symbol=symbol,
        native_symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
        quote_asset="USDT",
        entry_price=entry,
        leverage=leverage,
        take_profits=take_profits,
        stop_loss=stop_loss,
        reference_price=display_price,
        display_price=display_price,
        initial_pnl_percent=Decimal("0.00"),
        signal_created_at=FIXED_TIME,
        rendered_at=FIXED_TIME,
        elapsed_seconds=0,
    )


def _preview_display_price(
    *,
    direction: TradeDirection,
    entry: Decimal,
    leverage: int,
    pnl_percent: Decimal,
) -> Decimal:
    """
    Produce a display price consistent with direction and requested PnL.

    Long profit:  display > entry
    Long loss:    display < entry
    Short profit: display < entry
    Short loss:   display > entry
    """
    if pnl_percent == Decimal("0"):
        return entry

    price_move = (
        abs(pnl_percent)
        / Decimal(leverage)
        / Decimal("100")
    )

    profitable = pnl_percent > Decimal("0")

    market_moves_up = (
        profitable
        if direction is TradeDirection.LONG
        else not profitable
    )

    multiplier = (
        Decimal("1") + price_move
        if market_moves_up
        else Decimal("1") - price_move
    )

    return (entry * multiplier).quantize(Decimal("0.0001"))


def _preview_payload(
    *,
    direction: TradeDirection,
    pnl_percent: Decimal,
    leverage: int = 10,
) -> SignalRenderPayload:
    display = _preview_display_price(
        direction=direction,
        entry=BASE_ENTRY,
        leverage=leverage,
        pnl_percent=pnl_percent,
    )

    return _payload(
        direction=direction,
        entry=BASE_ENTRY,
        display=display,
        leverage=leverage,
    )


def _preview_pnl_text(
    exchange: Exchange,
    pnl_percent: Decimal,
) -> str:
    if pnl_percent > Decimal("0"):
        return f"+{pnl_percent:.2f}%"

    if pnl_percent < Decimal("0"):
        return f"{pnl_percent:.2f}%"

    # Approved Toobit neutral format.
    if exchange is Exchange.TOOBIT:
        return "+0.00%"

    return "0.00%"


# Existing calibration and edge-case fixtures.
# These names and behaviors remain unchanged.
FIXTURES: dict[str, SignalRenderPayload] = {
    "composite-long": _payload(),
    "composite-short": _payload(
        direction=TradeDirection.SHORT,
    ),
    "single-long": _payload(
        source=PriceSourceType.OURBIT_ONLY,
    ),
    "single-short": _payload(
        direction=TradeDirection.SHORT,
        source=PriceSourceType.OURBIT_ONLY,
    ),
    "long-symbol": _payload(
        symbol="VERYLONGCANONICALTOKENUSDT",
    ),
    "small-price": _payload(
        entry=Decimal("0.000000001234500"),
    ),
    "high-leverage": _payload(
        leverage=1000,
    ),
}


# Renderer-Lab-only value overrides.
# Existing fixtures intentionally have no overrides.
FIXTURE_VALUE_OVERRIDES: dict[str, dict[str, str]] = {}


# Preview fixtures are tied to one exchange so that an LBank-specific fixture
# cannot accidentally be rendered using another exchange.
PREVIEW_FIXTURE_EXCHANGES: dict[str, Exchange] = {}


_PREVIEW_EXCHANGES = (
    Exchange.OURBIT,
    Exchange.TOOBIT,
    Exchange.LBANK,
)

_PREVIEW_DIRECTIONS = (
    ("long", TradeDirection.LONG),
    ("short", TradeDirection.SHORT),
)

_PREVIEW_STATES = (
    ("profit", PREVIEW_PNL_MAGNITUDE),
    ("neutral", Decimal("0.00")),
    ("loss", -PREVIEW_PNL_MAGNITUDE),
)


# Adds 18 fixtures:
#
# ourbit-long-profit
# ourbit-long-neutral
# ourbit-long-loss
# ourbit-short-profit
# ...
# lbank-short-loss
for preview_exchange in _PREVIEW_EXCHANGES:
    for direction_name, preview_direction in _PREVIEW_DIRECTIONS:
        for state_name, preview_pnl in _PREVIEW_STATES:
            fixture_name = (
                f"{preview_exchange.value}-"
                f"{direction_name}-"
                f"{state_name}"
            )

            FIXTURES[fixture_name] = _preview_payload(
                direction=preview_direction,
                pnl_percent=preview_pnl,
            )

            FIXTURE_VALUE_OVERRIDES[fixture_name] = {
                "pnl": _preview_pnl_text(
                    preview_exchange,
                    preview_pnl,
                ),
            }

            PREVIEW_FIXTURE_EXCHANGES[fixture_name] = preview_exchange


def fixture_value_overrides(name: str) -> Mapping[str, str]:
    """
    Return Renderer-Lab-only visible-value overrides.

    Old fixtures return an empty mapping.
    """
    return dict(FIXTURE_VALUE_OVERRIDES.get(name, {}))


def fixture(
    name: str,
    exchange: Exchange,
    logo: Path | None = None,
) -> SignalRenderPayload:
    try:
        value = FIXTURES[name]
    except KeyError as exc:
        raise KeyError(f"unknown fixture: {name}") from exc

    expected_exchange = PREVIEW_FIXTURE_EXCHANGES.get(name)

    if expected_exchange is not None and expected_exchange is not exchange:
        raise KeyError(
            f"fixture {name} belongs to "
            f"{expected_exchange.value}, not {exchange.value}"
        )

    source = value.price_source_type

    if source is not PriceSourceType.COMPOSITE:
        source = {
            Exchange.OURBIT: PriceSourceType.OURBIT_ONLY,
            Exchange.TOOBIT: PriceSourceType.TOOBIT_ONLY,
            Exchange.LBANK: PriceSourceType.LBANK_ONLY,
        }[exchange]

    return replace(
        value,
        exchange=exchange,
        price_source_type=source,
        coin_logo_path=logo,
    )