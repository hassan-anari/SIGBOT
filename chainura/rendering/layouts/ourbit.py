from .models import *
from chainura.domain.destination import Exchange
LAYOUT=InitialCardLayout(Exchange.OURBIT,(
 TextLayer("symbol",Rectangle(100,160,980,260),FontSpec("bold",72,28),HorizontalAlignment.CENTER),
 TextLayer("direction",Rectangle(100,285,500,365),FontSpec("semibold",48,24)),
 TextLayer("leverage",Rectangle(580,285,980,365),FontSpec("semibold",48,24),HorizontalAlignment.RIGHT),
 TextLayer("entry",Rectangle(100,500,980,590),FontSpec("medium",54,22),HorizontalAlignment.RIGHT,prefix="Entry  "),
 TextLayer("price",Rectangle(100,630,980,720),FontSpec("medium",54,22),HorizontalAlignment.RIGHT,prefix="Fair Price  "),
 TextLayer("pnl",Rectangle(100,760,980,850),FontSpec("semibold",58,26),HorizontalAlignment.RIGHT,prefix="PnL  "),
 TextLayer("time",Rectangle(100,930,980,1010),FontSpec("regular",44,22),HorizontalAlignment.CENTER),
),ImageLayer("coin_logo",Rectangle(40,40,140,140)))
