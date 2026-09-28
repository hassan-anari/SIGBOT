"""Privacy-safe normalization for durable Telegram delivery workers."""
from __future__ import annotations
from dataclasses import dataclass
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
    TelegramNetworkError, TelegramRetryAfter, TelegramServerError)

@dataclass(frozen=True, slots=True)
class TelegramDeliveryFailure:
    category: str
    retry_after: float | None = None
    definite_no_send: bool = False

def normalize_telegram_error(exc: BaseException) -> TelegramDeliveryFailure:
    """Classify an exception without retaining its potentially sensitive text."""
    if isinstance(exc, TelegramRetryAfter):
        return TelegramDeliveryFailure("rate_limited", max(0.0, float(exc.retry_after)), True)
    if isinstance(exc, TelegramForbiddenError):
        return TelegramDeliveryFailure("insufficient_rights", definite_no_send=True)
    if isinstance(exc, TelegramBadRequest):
        reason = str(exc).casefold()
        if "reply message not found" in reason or "message to be replied not found" in reason:
            category = "reply_parent_not_found"
        elif "caption is too long" in reason:
            category = "caption_too_long"
        elif "chat not found" in reason:
            category = "chat_not_found"
        elif "message" in reason and ("invalid" in reason or "empty" in reason):
            category = "message_invalid"
        else:
            category = "telegram_bad_request"
        return TelegramDeliveryFailure(category, definite_no_send=True)
    if isinstance(exc, (TelegramNetworkError, TelegramServerError, TimeoutError, ConnectionError)):
        return TelegramDeliveryFailure("delivery_ambiguous")
    return TelegramDeliveryFailure("delivery_ambiguous")
