from .models import *
from chainura.domain.destination import Exchange

WHITE = (245, 246, 248, 255)
GRAY = (137, 139, 149, 255)
PNL_POSITIVE = (10, 176, 86, 255)
PNL_NEGATIVE = (239, 63, 63, 255)
PNL_NEUTRAL = (245, 246, 248, 255)


LAYOUT = InitialCardLayout(
    Exchange.OURBIT,
    (
        # BTCUSDT
        TextLayer(
            "symbol",
            Rectangle(48, 360, 560, 435),
            FontSpec("bold", 50, 28),
            HorizontalAlignment.LEFT,
            VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # Perp | Short | 25X
        TextLayer(
            "meta",
            Rectangle(48, 450, 500, 490),
            FontSpec("semibold", 37, 21),
            HorizontalAlignment.LEFT,
            VerticalAlignment.CENTER,
            fill=GRAY,
        ),

        # Initial PnL value.
        TextLayer(
            "pnl",
            Rectangle(48, 535, 520, 640),
            FontSpec("bold", 90, 40),
            HorizontalAlignment.LEFT,
            VerticalAlignment.CENTER,
            fill=PNL_NEUTRAL,
            fill_by_state=(
                ("positive", PNL_POSITIVE),
                ("negative", PNL_NEGATIVE),
                ("neutral", PNL_NEUTRAL),
            ),
        ),

        # The Entry Price label and $ sign are already part of the PNG.
        # This layer renders only the numeric value.
        TextLayer(
            "entry",
            Rectangle(375, 700, 650, 755),
            FontSpec("bold", 37, 24),
            HorizontalAlignment.LEFT,
            VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # The Fair Price label and $ sign are already part of the PNG.
        # This layer renders only the numeric value.
        TextLayer(
            "price",
            Rectangle(375, 755, 650, 820),
            FontSpec("bold", 37, 24),
            HorizontalAlignment.LEFT,
            VerticalAlignment.CENTER,
            fill=WHITE,
        ),

        # 00H 00M
        TextLayer(
            "time",
            Rectangle(50, 1025, 275, 1080),
            FontSpec("regular", 37, 20),
            HorizontalAlignment.LEFT,
            VerticalAlignment.CENTER,
            fill=GRAY,
        ),
    ),
    None,
)
