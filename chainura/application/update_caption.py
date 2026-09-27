from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from html import escape
from chainura.domain.monitoring import SignalEventKind
LRI='\u2066';PDI='\u2069';MAX_TELEGRAM_PHOTO_CAPTION=1024
class UpdateCaptionError(ValueError):pass
def iso(v):return f"{LRI}{escape(str(v),quote=False)}{PDI}"
def fmt_decimal(v:Decimal):
 s=format(v,'f')
 if '.' in s:s=s.rstrip('0').rstrip('.')
 return iso(s)
@dataclass(frozen=True,slots=True)
class UpdateCaptionInput:
 kind:SignalEventKind;canonical_symbol:str;observed_reference_price:Decimal;leveraged_roi:Decimal;tp_index:int|None=None
def build_update_caption(data:UpdateCaptionInput)->str:
 symbol=iso(data.canonical_symbol)
 if data.kind is SignalEventKind.TP_HIT:
  text=f"🎯 <b>تارگت {iso(data.tp_index)} {symbol} لمس شد</b>"
 elif data.kind is SignalEventKind.SL_HIT:
  text=f"🛑 <b>حد ضرر {symbol} فعال شد</b>"
 elif data.kind in (SignalEventKind.BREAKEVEN,SignalEventKind.RISK_FREE_HIT):
  text=f"⚖️ <b>سیگنال {symbol} در بریک‌ایون بسته شد</b>"
 else:raise UpdateCaptionError('unsupported update kind')
 if len(text)>MAX_TELEGRAM_PHOTO_CAPTION:raise UpdateCaptionError('update caption exceeds Telegram photo limit')
 return text
