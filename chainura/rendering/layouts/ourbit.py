from .models import *
LAYOUT=InitialCardLayout("ourbit",(
 TextLayer("symbol",Rectangle(100,160,980,260),FontSpec("main",72,28),HorizontalAlignment.CENTER),
 TextLayer("direction",Rectangle(100,285,500,365),FontSpec("main",48,24)),
 TextLayer("leverage",Rectangle(580,285,980,365),FontSpec("main",48,24),HorizontalAlignment.RIGHT),
 TextLayer("entry",Rectangle(100,500,980,590),FontSpec("main",54,22),HorizontalAlignment.RIGHT,prefix="Entry  "),
 TextLayer("price",Rectangle(100,630,980,720),FontSpec("main",54,22),HorizontalAlignment.RIGHT,prefix="Fair Price  "),
 TextLayer("pnl",Rectangle(100,760,980,850),FontSpec("main",58,26),HorizontalAlignment.RIGHT,prefix="PnL  "),
 TextLayer("time",Rectangle(100,930,980,1010),FontSpec("main",44,22),HorizontalAlignment.CENTER),
),ImageLayer("coin_logo",Rectangle(40,40,140,140)))
