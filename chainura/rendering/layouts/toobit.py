from .models import *
from chainura.domain.destination import Exchange
LAYOUT=InitialCardLayout(Exchange.TOOBIT,(
 TextLayer("symbol",Rectangle(90,130,852,220),FontSpec("bold",64,26),HorizontalAlignment.CENTER),
 TextLayer("direction",Rectangle(90,250,450,325),FontSpec("semibold",44,22)),
 TextLayer("leverage",Rectangle(500,250,852,325),FontSpec("semibold",44,22),HorizontalAlignment.RIGHT),
 TextLayer("entry",Rectangle(90,430,852,510),FontSpec("medium",48,20),HorizontalAlignment.RIGHT,prefix="Entry  "),
 TextLayer("price",Rectangle(90,550,852,630),FontSpec("medium",48,20),HorizontalAlignment.RIGHT,prefix="Last Price  "),
 TextLayer("pnl",Rectangle(90,670,852,750),FontSpec("semibold",54,24),HorizontalAlignment.RIGHT,prefix="PnL  "),
 TextLayer("time",Rectangle(90,850,852,920),FontSpec("regular",36,18),HorizontalAlignment.CENTER),
),ImageLayer("coin_logo",Rectangle(30,30,120,120)))
