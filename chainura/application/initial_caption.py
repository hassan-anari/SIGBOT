from __future__ import annotations

from chainura.domain.signal import TradeDirection
from chainura.errors import CaptionGenerationError
from chainura.telegram.presentation.formatting import format_decimal, format_leverage, format_symbol

TELEGRAM_PHOTO_CAPTION_MAX_LENGTH = 1024
_TP_NUMBERS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")


def build_initial_caption(signal, *, private: bool) -> str:
    """Build the complete deterministic, HTML-safe, BiDi-isolated Persian caption."""
    direction = "لانگ" if signal.direction is TradeDirection.LONG else "شورت"
    targets = []
    for position, price in enumerate(signal.take_profits, 1):
        marker = _TP_NUMBERS[position - 1] if position <= len(_TP_NUMBERS) else f"{position}."
        targets.append(f"{marker} {format_decimal(price)}")
    caption = (
        f"📊 <b>سیگنال {format_symbol(signal.canonical_symbol)}</b>\n"
        f"📈 جهت: <b>{direction}</b>\n"
        f"🎯 ورود: {format_decimal(signal.entry_price)}\n"
        f"⚡️ اهرم: {format_leverage(signal.leverage)}\n\n"
        "اهداف:\n" + "\n".join(targets) +
        f"\n\n🛑 حد ضرر: {format_decimal(signal.stop_loss)}"
    )
    if private:
        caption += "\n\n#پیامک"
    if len(caption) > TELEGRAM_PHOTO_CAPTION_MAX_LENGTH:
        raise CaptionGenerationError("initial photo caption exceeds Telegram limit")
    return caption
