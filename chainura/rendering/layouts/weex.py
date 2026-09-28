from .models import *
from chainura.domain.destination import Exchange


# Main foreground values.
WHITE = (250, 250, 250, 255)
MUTED = (154, 154, 158, 255)
SEPRATOR = (60, 60, 60, 255)

# WEEX direction colors from the supplied references.
LONG_TEXT = (72, 168, 135, 255)
SHORT_TEXT = (218, 75, 73, 255)

# Large PnL colors from the supplied references.
PNL_POSITIVE = (38, 192, 140, 255)
PNL_NEGATIVE = (235, 76, 66, 255)
PNL_NEUTRAL = WHITE


LAYOUT = InitialCardLayout(
    Exchange.WEEX,
    (
        # BTCUSDT
        TextLayer(
            field="symbol",
            box=Rectangle(45, 204, 245, 262),
            font=FontSpec("medium", 46, 30),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # Perp
        # Slightly lower so it sits visually centered against the symbol text.
        TextLayer(
            field="weex_perp",
            box=Rectangle(277, 205, 340, 268),
            font=FontSpec("medium", 31, 22),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # Long / Short
        TextLayer(
            field="weex_direction",
            box=Rectangle(45, 282, 140, 350),
            font=FontSpec("medium", 34, 23),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=LONG_TEXT,
            fill_by_state=(
                ("long", LONG_TEXT),
                ("short", SHORT_TEXT),
            ),
        ),

        # |
        TextLayer(
            field="weex_separator",
            box=Rectangle(120, 278, 160, 350),
            font=FontSpec("regular", 30, 23),
            horizontal=HorizontalAlignment.CENTER,
            vertical=VerticalAlignment.CENTER,
            fill=SEPRATOR,
        ),

        # 20x
        TextLayer(
            field="weex_leverage",
            box=Rectangle(155, 278, 255, 350),
            font=FontSpec("medium", 34, 23),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=LONG_TEXT,
            fill_by_state=(
                ("long", LONG_TEXT),
                ("short", SHORT_TEXT),
            ),
        ),

        # Large initial PnL.
        TextLayer(
            field="pnl",
            box=Rectangle(45, 370, 405, 490),
            font=FontSpec("bold", 90, 48),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=PNL_NEUTRAL,
            fill_by_state=(
                ("positive", PNL_POSITIVE),
                ("negative", PNL_NEGATIVE),
                ("neutral", PNL_NEUTRAL),
            ),
        ),

        # Numeric Entry price only.
        # "Entry price" is baked into the WEEX PNG template.
        TextLayer(
            field="entry",
            box=Rectangle(232, 748, 500, 808),
            font=FontSpec("regular", 34, 23),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # Numeric Mark price only.
        # "Mark price" is baked into the WEEX PNG template.
        TextLayer(
            field="price",
            box=Rectangle(227, 815, 500, 877),
            font=FontSpec("regular", 34, 23),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # Example:
        # 09/27 15:43:48
        #
        # "Time shared:" is baked into the template.
        TextLayer(
            field="weex_time",
            box=Rectangle(222, 880, 520, 944),
            font=FontSpec("regular", 29, 19),
            horizontal=HorizontalAlignment.LEFT,
            vertical=VerticalAlignment.CENTER,
            fill=MUTED,
        ),
    ),

    # WEEX logo is already baked into the official template.
    None,

    # No inline rows are needed anymore; symbol/perp and meta are now split
    # into independent text layers for exact positioning and coloring control.
    (),
)
