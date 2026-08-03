from .models import *
from chainura.domain.destination import Exchange
LAYOUT=InitialCardLayout(Exchange.LBANK,(
 TextLayer("symbol",Rectangle(75,125,755,215),FontSpec("bold",60,24),HorizontalAlignment.CENTER),
 TextLayer("direction",Rectangle(75,245,390,320),FontSpec("semibold",42,20)),
 TextLayer("leverage",Rectangle(440,245,755,320),FontSpec("semibold",42,20),HorizontalAlignment.RIGHT),
 TextLayer("entry",Rectangle(75,430,755,505),FontSpec("medium",44,18),HorizontalAlignment.RIGHT,prefix="Entry  "),
 TextLayer("price",Rectangle(75,540,755,615),FontSpec("medium",44,18),HorizontalAlignment.RIGHT,prefix="Last Price  "),
 TextLayer("pnl",Rectangle(75,650,755,725),FontSpec("semibold",50,22),HorizontalAlignment.RIGHT,prefix="PnL  "),
 TextLayer("time",Rectangle(75,835,755,905),FontSpec("regular",32,16),HorizontalAlignment.CENTER),
),ImageLayer("coin_logo",Rectangle(25,25,110,110)))
