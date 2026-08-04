from .models import *
from chainura.domain.destination import Exchange


WHITE = (245, 246, 248, 255)
MUTED = (126, 127, 132, 255)
DIVIDER = (69, 69, 69, 255)

LONG_TEXT = (58, 170, 114, 255)
SHORT_TEXT = (177, 72, 102, 255)

PNL_POSITIVE = (37, 190, 123, 255)
PNL_NEGATIVE = (201, 62, 98, 255)
PNL_NEUTRAL = (245, 246, 248, 255)


LAYOUT = InitialCardLayout(
    Exchange.LBANK,
    (
        # Initial PnL value only.
        # "PnL %" should already be baked into the template PNG.
        TextLayer(
            field="pnl",
            box=Rectangle(40, 270, 320, 405),
            font=FontSpec("semibold", 45, 25),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=PNL_NEUTRAL,
            fill_by_state=(("positive", PNL_POSITIVE),
                           ("negative", PNL_NEGATIVE),
                           ("neutral", PNL_NEUTRAL)),
        ),

        # Numeric Entry Price only.
        # "Entry Price" should already exist in the template.
        TextLayer(
            field="entry",
            box=Rectangle(40, 453, 320, 505),
            font=FontSpec("medium", 40, 22),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # Numeric Last Price only.
        # "Last Price" should already exist in the template.
        TextLayer(
            field="price",
            box=Rectangle(40, 590, 320, 635),
            font=FontSpec("medium", 40, 22),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # Tehran date and time in one line:
        # 2026/08/01 17:36:13
        TextLayer(
            field="lbank_time",
            box=Rectangle(500, 25, 790, 72),
            font=FontSpec("regular", 28, 16),
            horizontal=HorizontalAlignment.RIGHT,
            vertical=VerticalAlignment.CENTER,
            fill=MUTED,
        ),
    ),

    # LBank logo is baked into the template.
    # There is no dynamic coin-logo layer in this reference.
    None,

    (
        InlineRowLayer(
            field="lbank_header",
            box=Rectangle(40, 180, 790, 245),
            gap=14,
            vertical=VerticalAlignment.CENTER,
            items=(
                # BTCUSDT Perp
                InlineTextItem(
                    field="symbol",
                    font=FontSpec("medium", 34, 20),
                    flexible=True,
                    suffix=" Perp",
                    fill=WHITE,
                ),

                # |
                InlineTextItem(
                    field="separator",
                    font=FontSpec("regular", 35, 20),
                    fill=DIVIDER,
                    suffix=" ",
                    prefix=" "
                ),

                # Open Long / Open Short
                InlineTextItem(
                    field="direction",
                    font=FontSpec("medium", 34, 20),
                    prefix="Open ",
                    fill=LONG_TEXT,
                    fill_by_value=(
                        ("Short", SHORT_TEXT),
                    ),

                ),

                # |
                InlineTextItem(
                    field="separator",
                    font=FontSpec("regular", 35, 20),
                    fill=DIVIDER,
                    suffix=" ",
                    prefix=" "
                ),

                # 25x
                InlineTextItem(
                    field="lbank_leverage",
                    font=FontSpec("medium", 34, 20),
                    fill=WHITE,
                ),
            ),
        ),
    ),
)
