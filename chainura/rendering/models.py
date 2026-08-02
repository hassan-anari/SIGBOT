from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path

from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType, TradeDirection
from chainura.errors import InvalidSignalInputError

class SignalRenderEventKind(str, Enum): INITIAL="initial"
class TimeDisplayStrategy(str, Enum): ELAPSED="elapsed"; TEHRAN_WALL_CLOCK="tehran_wall_clock"; UTC_WALL_CLOCK="utc_wall_clock"
TIME_DISPLAY_STRATEGIES={Exchange.OURBIT:TimeDisplayStrategy.ELAPSED,Exchange.TOOBIT:TimeDisplayStrategy.TEHRAN_WALL_CLOCK,Exchange.LBANK:TimeDisplayStrategy.UTC_WALL_CLOCK}

@dataclass(frozen=True, slots=True)
class SignalRenderPayload:
    signal_id:int; event_kind:SignalRenderEventKind; exchange:Exchange; direction:TradeDirection
    price_source_type:PriceSourceType; canonical_symbol:str; native_symbol:str
    base_asset:str; quote_asset:str; entry_price:Decimal; leverage:int
    take_profits:tuple[Decimal,...]; stop_loss:Decimal; reference_price:Decimal
    display_price:Decimal; initial_pnl_percent:Decimal; signal_created_at:datetime
    rendered_at:datetime; elapsed_seconds:int; coin_logo_path:Path|None=None
    def __post_init__(self):
        if self.event_kind is not SignalRenderEventKind.INITIAL: raise InvalidSignalInputError("only initial rendering is supported")
        if self.display_price != self.reference_price: raise InvalidSignalInputError("initial display price must equal reference price")
        if self.initial_pnl_percent != Decimal("0"): raise InvalidSignalInputError("initial PnL must be zero")
        if self.elapsed_seconds != 0: raise InvalidSignalInputError("initial elapsed duration must be zero")
        for name in ("signal_created_at","rendered_at"):
            value=getattr(self,name)
            if value.tzinfo is None or value.utcoffset() is None: raise InvalidSignalInputError(f"{name} must be aware")

@dataclass(frozen=True, slots=True)
class RenderedSignalImage:
    file_path:Path; width:int; height:int; content_type:str; sha256:str
