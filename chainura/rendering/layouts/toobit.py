from .models import *
from chainura.domain.destination import Exchange


# Base text colors
WHITE = (245, 246, 248, 255)
MUTED = (125, 131, 148, 255)

LONG_TEXT = (24, 163, 105, 255)
LONG_BACKGROUND = (18, 37, 34, 255)

SHORT_TEXT = (255, 66, 93, 255)
SHORT_BACKGROUND = (41, 23, 28, 255)

LEVERAGE_TEXT = (255, 255, 255, 255)
LEVERAGE_BACKGROUND = (35, 37, 41, 255)

PNL_POSITIVE = (22, 182, 117, 255)
PNL_NEGATIVE = (255, 63, 88, 255)
PNL_NEUTRAL = (245, 246, 248, 255)


LAYOUT = InitialCardLayout(
    Exchange.TOOBIT,
    (
        # Initial PnL: 0.00%
        #
        # For visual negative-PnL calibration, temporarily change fill
        # to PNL_NEGATIVE in the local layout only.
        TextLayer(
            field="pnl",
            box=Rectangle(48, 300, 410, 390),
            font=FontSpec("bold", 88, 42),
            horizontal=HorizontalAlignment.LEFT,
             prefix="+",
            vertical=VerticalAlignment.CENTER,
            fill=PNL_NEUTRAL,
        ),

        # Numeric Entry value only.
        # "Entry price (USDT)" must already exist in the PNG.
        TextLayer(
            field="entry",
            box=Rectangle(46, 490, 410, 548),
            font=FontSpec("medium", 42, 25),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # Numeric Last Price value only.
        # "Last price (USDT)" must already exist in the PNG.
        TextLayer(
            field="price",
            box=Rectangle(46, 618, 410, 678),
            font=FontSpec("medium", 42, 25),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # Tehran clock, for example: 16:04:56
        TextLayer(
            field="time_clock",
            box=Rectangle(650, 50, 895, 108),
            font=FontSpec("regular", 28, 17),
            horizontal=HorizontalAlignment.RIGHT,
            prefix="(UTC +3) ",
            vertical=VerticalAlignment.CENTER,
            fill=MUTED,
        ),

        # Tehran date, for example: 2026-08-01
        TextLayer(
            field="time_date",
            box=Rectangle(650, 80, 895, 145),
            font=FontSpec("regular", 26, 16),
            horizontal=HorizontalAlignment.RIGHT,
            vertical=VerticalAlignment.CENTER,
            fill=MUTED,
        ),
    ),

    # Dynamic coin logo beside the Symbol.
    ImageLayer(
        "coin_logo",
        Rectangle(48, 205, 106, 276),
    ),
    (InlineRowLayer(
        field="toobit_header",
        box=Rectangle(120, 210, 750, 272),
        gap=8,
        vertical=VerticalAlignment.CENTER,
        items=(
            InlineTextItem("symbol", FontSpec("bold", 50, 25), flexible=True,
                           fill=WHITE),
            InlineTextItem("direction", FontSpec("medium", 31, 20),
                fill=LONG_TEXT, fill_by_value=(("Short", SHORT_TEXT),),
                badge=BadgeStyle(fill=LONG_BACKGROUND, radius=9, padding_x=12,
                    padding_y=7, fill_by_value=(("Short", SHORT_BACKGROUND),))),
            InlineTextItem("leverage", FontSpec("medium", 29, 20),
                fill=LEVERAGE_TEXT, badge=BadgeStyle(fill=LEVERAGE_BACKGROUND,
                    radius=9, padding_x=11, padding_y=9)),
        ),
    ),)
)
