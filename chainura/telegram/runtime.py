from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.middleware import FSMContextMiddleware
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.memory import DisabledEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.strategy import FSMStrategy
from aiogram.types import BotCommand
from redis.asyncio import Redis

from chainura.application.access import ActorResolver
from chainura.application.signalist_service import SignalistService
from chainura.config import TelegramRuntimeSettings, load_telegram_runtime_settings
from chainura.errors import TelegramRuntimeError
from chainura.persistence.database import create_database_engine, create_session_factory
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.telegram.middlewares import (ActorResolutionMiddleware, IdempotencyMiddleware,
                                            PrivateChatGate, TelegramErrorBoundary)
from chainura.telegram.presentation import texts
from chainura.telegram.redis_runtime import (FSM_TTL_SECONDS, PollerLease, UpdateIdempotency,
                                             bot_identity)
from chainura.telegram.routers import router

LOGGER = logging.getLogger("chainura.telegram.runtime")
SHUTDOWN_TIMEOUT = 15


def create_production_storage(redis: Redis, identity: str) -> RedisStorage:
    builder = DefaultKeyBuilder(prefix=f"chainura:tg:{identity}:fsm", separator=":")
    return RedisStorage(redis=redis, key_builder=builder, state_ttl=FSM_TTL_SECONDS,
                        data_ttl=FSM_TTL_SECONDS)


def build_dispatcher(storage: RedisStorage, idempotency: UpdateIdempotency,
                     resolver: ActorResolver) -> Dispatcher:
    dispatcher = Dispatcher(disable_fsm=True)
    production_fsm = FSMContextMiddleware(storage=storage,
        events_isolation=DisabledEventIsolation(), strategy=FSMStrategy.USER_IN_CHAT)
    dispatcher.update.outer_middleware(TelegramErrorBoundary())
    dispatcher.update.outer_middleware(PrivateChatGate())
    dispatcher.update.outer_middleware(IdempotencyMiddleware(idempotency))
    dispatcher.update.outer_middleware(production_fsm)
    dispatcher.update.outer_middleware(ActorResolutionMiddleware(resolver))
    dispatcher.include_router(router)
    return dispatcher


class Runtime:
    def __init__(self, settings: TelegramRuntimeSettings) -> None:
        self.settings = settings
        self.engine = None
        self.redis: Redis | None = None
        self.storage: RedisStorage | None = None
        self.bot: Bot | None = None
        self.dispatcher: Dispatcher | None = None
        self.lease: PollerLease | None = None
        self.polling_task: asyncio.Task[None] | None = None
        self.renewal_task: asyncio.Task[None] | None = None
        self.renewal_stop = asyncio.Event()
        self.lease_lost = asyncio.Event()
        self._cleanup_lock = asyncio.Lock()
        self._stopped = False
        self._storage_owns_redis = False

    async def start(self) -> None:
        identity = bot_identity(self.settings.bot_token)
        self.engine = create_database_engine(self.settings.application.database_url)
        sessions = create_session_factory(self.engine)
        service = SignalistService(SqlAlchemySignalistTransactionFactory(sessions))
        resolver = ActorResolver(self.settings.application, service)
        try:
            self.redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
            await self.redis.ping()
            self.lease = PollerLease(self.redis, identity)
            if not await self.lease.acquire():
                raise TelegramRuntimeError("another poller is active")
            self.bot = Bot(self.settings.bot_token,
                           default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            self.storage = create_production_storage(self.redis, identity)
            self._storage_owns_redis = True
            idempotency = UpdateIdempotency(self.redis, identity)
            self.dispatcher = build_dispatcher(self.storage, idempotency, resolver)
            await self.bot.set_my_commands([
                BotCommand(command="start", description=texts.COMMAND_START),
                BotCommand(command="help", description=texts.COMMAND_HELP),
            ])
            await self.bot.delete_webhook(drop_pending_updates=False)
            self.renewal_task = asyncio.create_task(
                self.lease.run_renewal(self.renewal_stop, self.lease_lost))
            self.polling_task = asyncio.create_task(self.dispatcher.start_polling(
                self.bot, handle_signals=False, close_bot_session=False,
                handle_as_tasks=False, allowed_updates=["message", "callback_query"]))
            lost_waiter = asyncio.create_task(self.lease_lost.wait())
            done, _ = await asyncio.wait({self.polling_task, lost_waiter},
                                         return_when=asyncio.FIRST_COMPLETED)
            if lost_waiter in done and self.lease_lost.is_set():
                raise TelegramRuntimeError("poller lease ownership was lost")
            lost_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await lost_waiter
            await self.polling_task
        finally:
            await self.cleanup()

    async def cleanup(self) -> None:
        async with self._cleanup_lock:
            if self._stopped:
                return
            if self.dispatcher and self.polling_task and not self.polling_task.done():
                try:
                    await asyncio.wait_for(self.dispatcher.stop_polling(), SHUTDOWN_TIMEOUT)
                except (TimeoutError, RuntimeError):
                    self.polling_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await self.polling_task
            self.renewal_stop.set()
            if self.renewal_task:
                try:
                    await asyncio.wait_for(self.renewal_task, SHUTDOWN_TIMEOUT)
                except TimeoutError:
                    self.renewal_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self.renewal_task
            if self.lease:
                with suppress(TelegramRuntimeError):
                    await self.lease.release()
            if self.storage:
                await self.storage.close()
            elif self.redis:
                await self.redis.aclose(close_connection_pool=True)
            if self.bot:
                await self.bot.session.close()
            if self.engine:
                await self.engine.dispose()
            self._stopped = True


async def run() -> None:
    settings = load_telegram_runtime_settings()
    runtime = Runtime(settings)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
    task = asyncio.create_task(runtime.start())
    stop_task = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait({task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if stop_task in done:
        await runtime.cleanup()
    stop_task.cancel()
    with suppress(asyncio.CancelledError):
        await stop_task
    await task
