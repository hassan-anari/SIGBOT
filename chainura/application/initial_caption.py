from __future__ import annotations
from chainura.domain.signal import TradeDirection
from chainura.errors import CaptionGenerationError
from chainura.telegram.presentation.formatting import format_decimal, format_leverage, format_symbol

TELEGRAM_PHOTO_CAPTION_MAX_LENGTH=1024
_TP_NUMBERS=("1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟")

def build_initial_caption(signal, *, private: bool) -> str:
    """The Stage-One photo deliberately carries no complete TP/SL caption."""
    return f"📊 <b>شات اولیه {format_symbol(signal.canonical_symbol)}</b>"

def build_signal_details_text(signal, *, private: bool) -> str:
    """Replaceable Phase-7.3 boundary for the safe default details template."""
    if not signal.take_profits or signal.stop_loss is None:
        raise CaptionGenerationError("active signal details are incomplete")
    direction="لانگ" if signal.direction is TradeDirection.LONG else "شورت"
    targets=[]
    for position,price in enumerate(signal.take_profits,1):
        marker=_TP_NUMBERS[position-1] if position<=len(_TP_NUMBERS) else f"{position}."
        targets.append(f"{marker} {format_decimal(price)}")
    text=(f"📊 <b>سیگنال {format_symbol(signal.canonical_symbol)}</b>\n"
          f"📈 جهت: <b>{direction}</b>\n"
          f"🎯 ورود: {format_decimal(signal.entry_price)}\n"
          f"⚡️ اهرم: {format_leverage(signal.leverage)}\n\nاهداف:\n"
          +"\n".join(targets)+f"\n\n🛑 حد ضرر: {format_decimal(signal.stop_loss)}")
    if private:text+="\n\n#پیامک"
    if len(text)>4096:raise CaptionGenerationError("signal details exceed Telegram message limit")
    return text
