from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType, TradeDirection
from chainura.rendering.models import SignalRenderEventKind, SignalRenderPayload

FIXED_TIME=datetime(2026,8,1,12,34,56,tzinfo=timezone.utc)
def _payload(direction=TradeDirection.LONG,symbol="BTCUSDT",entry=Decimal("65234.1200"),leverage=10,source=PriceSourceType.COMPOSITE):
    long=direction is TradeDirection.LONG
    step=entry/Decimal("10")
    return SignalRenderPayload(501,SignalRenderEventKind.INITIAL,Exchange.OURBIT,direction,source,symbol,symbol,symbol.removesuffix("USDT"),"USDT",entry,leverage,(entry+step,entry+step*2) if long else (entry-step,entry-step*2),entry-step if long else entry+step,entry,entry,Decimal("0.00"),FIXED_TIME,FIXED_TIME,0)

FIXTURES={
 "composite-long":_payload(),
 "composite-short":_payload(TradeDirection.SHORT),
 "single-long":_payload(source=PriceSourceType.OURBIT_ONLY),
 "single-short":_payload(TradeDirection.SHORT,source=PriceSourceType.OURBIT_ONLY),
 "long-symbol":_payload(symbol="VERYLONGCANONICALTOKENUSDT"),
 "small-price":_payload(entry=Decimal("0.000000001234500")),
 "high-leverage":_payload(leverage=1000),
}

def fixture(name:str,exchange:Exchange,logo=None)->SignalRenderPayload:
    try: value=FIXTURES[name]
    except KeyError as exc: raise KeyError(f"unknown fixture: {name}") from exc
    source=value.price_source_type
    if source is not PriceSourceType.COMPOSITE: source={Exchange.OURBIT:PriceSourceType.OURBIT_ONLY,Exchange.TOOBIT:PriceSourceType.TOOBIT_ONLY,Exchange.LBANK:PriceSourceType.LBANK_ONLY}[exchange]
    return replace(value,exchange=exchange,price_source_type=source,coin_logo_path=logo)
