from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from chainura.application.access import ActorResolver
from chainura.telegram.presentation import texts
from chainura.telegram.redis_runtime import ClaimResult, UpdateIdempotency

LOGGER = logging.getLogger("chainura.telegram")
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class CallbackAcknowledger:
    def __init__(self, callback: CallbackQuery | None) -> None:
        self.callback = callback
        self.attempted = False
        self.succeeded = False
        self._lock = asyncio.Lock()

    async def answer_once(self, text: str | None = None) -> bool:
        async with self._lock:
            if self.callback is None or self.attempted:
                return False
            self.attempted = True
            await self.callback.answer(text=text)
            self.succeeded = True
            return True


def callback_from_update(update: Update) -> CallbackQuery | None:
    return update.callback_query


def response_message(update: Update) -> Message | None:
    if update.message:
        return update.message
    if update.callback_query and isinstance(update.callback_query.message, Message):
        return update.callback_query.message
    return None


class TelegramErrorBoundary(BaseMiddleware):
    async def __call__(self, handler: Handler, event: Update, data: dict[str, Any]) -> Any:
        acknowledger = CallbackAcknowledger(callback_from_update(event))
        data["callback_ack"] = acknowledger
        try:
            return await handler(event, data)
        except Exception:
            LOGGER.exception("telegram update failed update_id=%s", event.update_id)
            try:
                if event.callback_query:
                    await acknowledger.answer_once(texts.TEMPORARY_ERROR)
                else:
                    message = response_message(event)
                    if message:
                        await message.answer(texts.TEMPORARY_ERROR)
            except Exception:
                LOGGER.exception("telegram error response failed update_id=%s", event.update_id)
            return None


class PrivateChatGate(BaseMiddleware):
    async def __call__(self, handler: Handler, event: Update, data: dict[str, Any]) -> Any:
        message = response_message(event)
        if message is None or message.chat.type != "private":
            LOGGER.info("ignored non-private update update_id=%s", event.update_id)
            return None
        return await handler(event, data)


class IdempotencyMiddleware(BaseMiddleware):
    def __init__(self, service: UpdateIdempotency) -> None:
        self.service = service

    async def __call__(self, handler: Handler, event: Update, data: dict[str, Any]) -> Any:
        result, owner = await self.service.claim(event.update_id)
        if result is not ClaimResult.CLAIMED:
            await data["callback_ack"].answer_once(texts.DUPLICATE_CALLBACK)
            return None
        try:
            value = await handler(event, data)
            if not await self.service.finish(event.update_id, owner):
                raise RuntimeError("owned update claim was lost")
            return value
        except Exception:
            try:
                await self.service.release(event.update_id, owner)
            except Exception:
                LOGGER.exception("failed to release update claim update_id=%s", event.update_id)
            raise


class ActorResolutionMiddleware(BaseMiddleware):
    def __init__(self, resolver: ActorResolver) -> None:
        self.resolver = resolver

    async def __call__(self, handler: Handler, event: Update, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return None
        data["actor"] = await self.resolver.resolve(user.id)
        return await handler(event, data)
