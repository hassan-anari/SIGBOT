"""First-pass WEEX card schema; coordinates are intentionally Lab-tunable."""
from .models import *
from chainura.domain.destination import Exchange

WHITE=(245,246,248,255); MUTED=(150,155,165,255); GREEN=(30,200,140,255); RED=(230,75,95,255)
LAYOUT=InitialCardLayout(Exchange.WEEX,(
 TextLayer("symbol",Rectangle(70,120,930,220),FontSpec("bold",58,28),HorizontalAlignment.CENTER,VerticalAlignment.CENTER,WHITE),
 TextLayer("direction",Rectangle(70,235,450,305),FontSpec("semibold",40,22),HorizontalAlignment.LEFT,VerticalAlignment.CENTER,GREEN,fill_by_value=(("Short",RED),)),
 TextLayer("leverage",Rectangle(550,235,930,305),FontSpec("semibold",40,22),HorizontalAlignment.RIGHT,VerticalAlignment.CENTER,WHITE),
 TextLayer("entry",Rectangle(70,390,930,465),FontSpec("medium",42,22),HorizontalAlignment.CENTER,VerticalAlignment.CENTER,WHITE),
 TextLayer("price",Rectangle(70,530,930,605),FontSpec("medium",42,22),HorizontalAlignment.CENTER,VerticalAlignment.CENTER,WHITE),
 TextLayer("pnl",Rectangle(70,675,930,780),FontSpec("bold",62,28),HorizontalAlignment.CENTER,VerticalAlignment.CENTER,WHITE,fill_by_state=(("positive",GREEN),("negative",RED),("neutral",WHITE))),
 TextLayer("time",Rectangle(70,875,930,930),FontSpec("regular",26,16),HorizontalAlignment.CENTER,VerticalAlignment.CENTER,MUTED),
))
