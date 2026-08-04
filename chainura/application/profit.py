from decimal import Decimal
from chainura.domain.signal import TradeDirection
from chainura.errors import RenderPayloadError

def leveraged_return_percent(entry:Decimal,price:Decimal,leverage:int,direction:TradeDirection)->Decimal:
    if (not isinstance(entry,Decimal) or not entry.is_finite() or entry<=0
            or not isinstance(price,Decimal) or not price.is_finite() or price<=0
            or isinstance(leverage,bool) or not isinstance(leverage,int) or leverage<1
            or not isinstance(direction,TradeDirection)):
        raise RenderPayloadError("profit calculation inputs are invalid")
    difference=price-entry if direction is TradeDirection.LONG else entry-price
    return difference/entry*Decimal("100")*leverage
