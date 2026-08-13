from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path
from contextlib import suppress
from enum import Enum
from typing import Awaitable, TypeVar

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
from chainura.application.admin_signalist_service import AdminSignalistService
from chainura.application.destination_service import DestinationService
from chainura.application.destination_assignment_service import DestinationAssignmentService
from chainura.config import TelegramRuntimeSettings, load_telegram_runtime_settings
from chainura.errors import TelegramRuntimeError
from chainura.persistence.database import (create_database_engine, create_session_factory,
                                            initialize_database_engine)
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.telegram.middlewares import (ActorResolutionMiddleware, IdempotencyMiddleware,
                                            PrivateChatGate, TelegramErrorBoundary)
from chainura.telegram.presentation import texts
from chainura.telegram.redis_runtime import (FSM_TTL_SECONDS, PollerLease, UpdateIdempotency,
                                             bot_identity)
from chainura.telegram.routers import router
from chainura.telegram.admin_routes import router as admin_router
from chainura.telegram.signal_routes import router as signal_router
from chainura.telegram.template_routes import router as template_router
from chainura.telegram.message_template_routes import router as message_template_router
from chainura.telegram.event_routes import router as event_router
from chainura.telegram.active_signal_routes import router as active_signal_router
from chainura.application.signal_service import SignalService
from chainura.application.market_gateway import MarketReferencePriceProvider, SymbolCatalogProvider
from chainura.application.market_gateway import BatchedMarketSnapshotProvider, MarketPriceWorker
from chainura.application.monitoring import MonitoringService,MonitoringWorker,BreakevenSettings
from chainura.application.health import HealthRepository, AdminAlertWorker
from chainura.application.reporting import ReportingRepository, ReportingService
from chainura.application.signalist_notification import SignalistNotificationService,SignalistNotificationWorker
from chainura.telegram.event_routes import event_decision_keyboard, breakeven_prompt_keyboard
from chainura.domain.monitoring import NotificationKind
from market import MarketProbe
from chainura.telegram.destination_inspector import TelegramDestinationInspector
from chainura.rendering.logos import CoinLogoStore, ToobitLogoSynchronizer, run_logo_sync
from chainura.rendering.production import ProductionRendererRegistration
from chainura.rendering.templates import TemplateAssetStore, TemplateService
from chainura.application.initial_rendering import InitialPayloadFactory, InitialImageService
from chainura.application.publication import (InitialPublicationService, InitialPublicationWorker, UpdatePublicationService, UpdatePublicationWorker,
                                               SignalDetailsPublicationService, SignalDetailsPublicationWorker,
                                               TelegramPhotoPublisher)
from chainura.application.template_admin import TemplateAdministrationService
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.application.active_signals import ActiveSignalService
from chainura.application.publication import CancellationPublicationService,CancellationPublicationWorker
from chainura.application.signal_comments import SignalCommentService,SignalCommentPublicationService,SignalCommentPublicationWorker

LOGGER = logging.getLogger("chainura.telegram.runtime")
CLEANUP_OPERATION_TIMEOUT_SECONDS = 5
T = TypeVar("T")


class RuntimeState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


class RuntimeShutdownRequested(Exception):
    """Internal control flow used to stop partial startup safely."""


def create_production_storage(redis: Redis, identity: str) -> RedisStorage:
    builder = DefaultKeyBuilder(prefix=f"chainura:tg:{identity}:fsm", separator=":")
    return RedisStorage(redis=redis, key_builder=builder, state_ttl=FSM_TTL_SECONDS,
                        data_ttl=FSM_TTL_SECONDS)


def build_dispatcher(storage: RedisStorage, idempotency: UpdateIdempotency,
                     resolver: ActorResolver, **services) -> Dispatcher:
    dispatcher = Dispatcher(disable_fsm=True)
    production_fsm = FSMContextMiddleware(storage=storage,
        events_isolation=DisabledEventIsolation(), strategy=FSMStrategy.USER_IN_CHAT)
    dispatcher.update.outer_middleware(TelegramErrorBoundary())
    dispatcher.update.outer_middleware(PrivateChatGate())
    dispatcher.update.outer_middleware(IdempotencyMiddleware(idempotency))
    dispatcher.update.outer_middleware(production_fsm)
    dispatcher.update.outer_middleware(ActorResolutionMiddleware(resolver))
    dispatcher.include_router(admin_router)
    dispatcher.include_router(template_router)
    dispatcher.include_router(message_template_router)
    dispatcher.include_router(active_signal_router)
    dispatcher.include_router(signal_router)
    dispatcher.include_router(event_router)
    dispatcher.include_router(router)
    for name, service in services.items(): dispatcher[name] = service
    return dispatcher


class Runtime:
    def __init__(self, settings: TelegramRuntimeSettings) -> None:
        self.settings = settings
        self.state = RuntimeState.STARTING
        self.shutdown_requested = asyncio.Event()
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
        self.logo_sync_task: asyncio.Task[None] | None = None
        self.logo_sync_stop = asyncio.Event()
        self._cleanup_lock = asyncio.Lock()
        self._cleanup_complete = asyncio.Event()
        self._lease_released = False
        self._redis_closed = False
        self._bot_closed = False
        self._engine_disposed = False
        self.renderer_registration = None
        self.initial_image_service = None
        self.publication_worker = None
        self.publication_task = None
        self.update_publication_worker = None
        self.update_publication_task = None
        self.details_publication_worker = None
        self.details_publication_task = None
        self.monitoring_worker=None;self.monitoring_task=None
        self.market_worker=None;self.market_task=None
        self.notification_worker=None;self.notification_task=None
        self.admin_alert_worker=None;self.admin_alert_task=None
        self.cancellation_publication_worker=None;self.cancellation_publication_task=None
        self.comment_publication_worker=None;self.comment_publication_task=None

    def request_shutdown(self) -> None:
        if self.state is not RuntimeState.STOPPED:
            self.shutdown_requested.set()

    def _checkpoint(self) -> None:
        if self.shutdown_requested.is_set():
            raise RuntimeShutdownRequested

    def _renewal_done(self, task: asyncio.Task[None]) -> None:
        if not self.renewal_stop.is_set():
            self.lease_lost.set()
            self.request_shutdown()

    def _publication_done(self, task: asyncio.Task[None]) -> None:
        if self.state is RuntimeState.RUNNING and not self.shutdown_requested.is_set():
            self._observe_task_failure(task,"publication worker")
            self.request_shutdown()

    def _observed_worker_done(self, task: asyncio.Task[None], component: str) -> None:
        if self.state is RuntimeState.RUNNING and not self.shutdown_requested.is_set():
            self._observe_task_failure(task,component)
            self.request_shutdown()

    async def _startup_await(self, awaitable: Awaitable[T]) -> T:
        """Await one startup stage while renewal and shutdown remain supervised."""
        self._checkpoint()
        operation = asyncio.ensure_future(awaitable)
        shutdown_waiter = asyncio.create_task(self.shutdown_requested.wait())
        watched: set[asyncio.Future[object]] = {operation, shutdown_waiter}
        if self.renewal_task is not None:
            watched.add(self.renewal_task)
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        if operation in done and not self.shutdown_requested.is_set():
            shutdown_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await shutdown_waiter
            result = await operation
            self._checkpoint()
            return result
        if not operation.done():
            operation.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await operation
        shutdown_waiter.cancel()
        with suppress(asyncio.CancelledError):
            await shutdown_waiter
        if (
            self.renewal_task is not None
            and self.renewal_task.done()
            and not self.renewal_stop.is_set()
        ):
            cause = self._task_exception(self.renewal_task)
            self._observe_task_failure(self.renewal_task, "poller renewal")
            failure = TelegramRuntimeError(
                "poller lease renewal stopped during startup"
            )
            if cause is not None:
                raise failure from cause
            raise failure
        raise RuntimeShutdownRequested

    @staticmethod
    def _task_exception(task: asyncio.Task[object]) -> BaseException | None:
        if task.cancelled():
            return None
        try:
            return task.exception()
        except asyncio.CancelledError:
            return None

    @classmethod
    def _observe_task_failure(cls, task: asyncio.Task[object], component: str) -> None:
        exception = cls._task_exception(task)
        if exception is not None:
            LOGGER.error("%s task failed category=%s", component,
                         type(exception).__name__)

    async def start(self) -> None:
        identity = bot_identity(self.settings.bot_token)
        try:
            self._checkpoint()
            if self.settings.rendering is not None:
                self.renderer_registration=ProductionRendererRegistration(self.settings.rendering).start()
            self.engine = create_database_engine(self.settings.application.database_url)
            await self._startup_await(initialize_database_engine(self.engine))
            sessions = create_session_factory(self.engine)
            transactions = SqlAlchemySignalistTransactionFactory(sessions)
            service = SignalistService(transactions)
            resolver = ActorResolver(self.settings.application, service)
            self._checkpoint()
            self.redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
            await self._startup_await(self.redis.ping())
            self.lease = PollerLease(self.redis, identity)
            if not await self._startup_await(self.lease.acquire()):
                raise TelegramRuntimeError("another poller is active")

            # Renewal is supervised before any Telegram network operation begins.
            self.renewal_task = asyncio.create_task(
                self.lease.run_renewal(self.renewal_stop, self.lease_lost))
            self.renewal_task.add_done_callback(self._renewal_done)
            self._checkpoint()

            self.bot = Bot(self.settings.bot_token,
                           default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            self._checkpoint()
            self.storage = create_production_storage(self.redis, identity)
            idempotency = UpdateIdempotency(self.redis, identity)
            destination_service = DestinationService(transactions)
            assignment_service = DestinationAssignmentService(transactions)
            market_probe = MarketProbe()
            health_repository=HealthRepository(sessions)
            if hasattr(sessions,"class_"):
                await self._startup_await(health_repository.seed_unknown_services(market_probe.clock()))
            reporting_service=ReportingService(ReportingRepository(sessions))
            symbol_catalog = SymbolCatalogProvider(market_probe, health_repository)
            monitoring_service=None
            snapshot_provider=BatchedMarketSnapshotProvider(market_probe,symbol_catalog,health_repository)
            if hasattr(sessions,"class_"):
                monitoring_service=MonitoringService(sessions,snapshot_provider,
                BreakevenSettings(self.settings.breakeven_min_open_seconds,self.settings.breakeven_favorable_move_percent,self.settings.breakeven_execution_tolerance_percent,self.settings.breakeven_rearm_distance_percent,self.settings.breakeven_prompt_cooldown_seconds))
                await self._startup_await(monitoring_service.reconcile())
            self.market_worker = MarketPriceWorker(
                market_probe, symbol_catalog,
                monitoring_service.load_work if monitoring_service is not None else None,
                self.settings.market_price_poll_seconds,
            )
            symbol_catalog.market_service = self.market_worker
            class DirectTransport:
                async def send(inner,telegram_id,text,kind,source_id):
                    if kind is NotificationKind.EVENT:
                        markup=event_decision_keyboard(source_id)
                    else:
                        markup=breakeven_prompt_keyboard(source_id)
                    message=await self.bot.send_message(telegram_id,text,reply_markup=markup)
                    return message.message_id
            class AdminAlertTransport:
                async def send_message(inner,telegram_id,text):
                    return await self.bot.send_message(telegram_id,text)
            if hasattr(sessions,"class_"):
                self.admin_alert_worker = AdminAlertWorker(
                    health_repository,
                    AdminAlertTransport(),
                    self.settings.application.primary_main_admin_telegram_id,
                    concurrency=self.settings.admin_alert_concurrency,
                    max_attempts=self.settings.admin_alert_max_attempts,
                    retry_base_seconds=self.settings.admin_alert_retry_base_seconds,
                    sweep_seconds=self.settings.admin_alert_sweep_seconds,
                )
                health_repository.wake_alerts=self.admin_alert_worker.wake
            notification_service=None
            if monitoring_service is not None:
                notification_service=SignalistNotificationService(sessions,DirectTransport(),self.settings.signalist_notification_max_attempts,self.settings.signalist_notification_retry_base_seconds)
                await self._startup_await(notification_service.recover())
                self.notification_worker=SignalistNotificationWorker(notification_service,self.settings.signalist_notification_concurrency,self.settings.signalist_notification_sweep_seconds)
                monitoring_service.notification_wake=self.notification_worker.wake
                if self.settings.monitoring_enabled:
                    self.monitoring_worker=MonitoringWorker(monitoring_service,self.settings.monitoring_interval_seconds,self.settings.monitoring_batch_size,self.settings.monitoring_concurrency)
            template_admin_service=None
            message_template_service=DestinationMessageTemplateService(transactions)
            if hasattr(sessions,"class_"):
                cancellation_service=CancellationPublicationService(transactions,TelegramPhotoPublisher(self.bot),message_templates=message_template_service)
                self.cancellation_publication_worker=CancellationPublicationWorker(cancellation_service,self.settings.publication_concurrency,self.settings.publication_sweep_seconds)
                await self._startup_await(cancellation_service.recover());await self._startup_await(self.cancellation_publication_worker.start())
                self.cancellation_publication_task=self.cancellation_publication_worker.task
            active_signal_service=ActiveSignalService(transactions,cancellation_wake=self.cancellation_publication_worker.wake if self.cancellation_publication_worker else None)
            if self.settings.rendering is not None:
                template_service=TemplateService(transactions,TemplateAssetStore(self.settings.rendering.template_asset_root))
                self.initial_image_service=InitialImageService(InitialPayloadFactory(
                    CoinLogoStore(self.settings.rendering.toobit_logo_root)),template_service,
                    self.settings.rendering.output_root,self.settings.rendering.concurrency)
                photo_publisher=TelegramPhotoPublisher(self.bot)
                publication_service=InitialPublicationService(transactions,self.initial_image_service,
                    photo_publisher,max_attempts=self.settings.send_max_attempts,
                    retry_base_seconds=self.settings.send_retry_base_seconds,message_templates=message_template_service)
                self.publication_worker=InitialPublicationWorker(publication_service,
                    self.settings.publication_concurrency,self.settings.publication_sweep_seconds)
                update_publication_service=UpdatePublicationService(transactions,self.initial_image_service,
                    photo_publisher,max_attempts=self.settings.update_send_max_attempts,
                    retry_base_seconds=self.settings.update_send_retry_base_seconds,message_templates=message_template_service)
                self.update_publication_worker=UpdatePublicationWorker(update_publication_service,
                    self.settings.update_publication_concurrency,self.settings.update_publication_sweep_seconds)
                details_service=SignalDetailsPublicationService(transactions,photo_publisher,
                    max_attempts=self.settings.send_max_attempts,
                    retry_base_seconds=self.settings.send_retry_base_seconds,message_templates=message_template_service)
                self.details_publication_worker=SignalDetailsPublicationWorker(details_service,
                    self.settings.publication_concurrency,self.settings.publication_sweep_seconds)
                if monitoring_service is not None:
                    monitoring_service.update_publication_wake=self.update_publication_worker.wake
                await self._startup_await(publication_service.recover())
                await self._startup_await(update_publication_service.recover())
                await self._startup_await(self._cleanup_orphan_outputs())
                class BothTemplateService:
                    async def unblock_templates(_,):
                        await publication_service.unblock_templates();await update_publication_service.unblock_templates()
                def wake_templates():
                    self.publication_worker.wake();self.update_publication_worker.wake()
                template_admin_service=TemplateAdministrationService(template_service,
                    BothTemplateService(),wake_templates)
            if self.publication_worker is not None:
                await self._startup_await(self.publication_worker.start())
                self.publication_task=self.publication_worker.task
                self.publication_task.add_done_callback(self._publication_done)
            if self.update_publication_worker is not None:
                await self._startup_await(self.update_publication_worker.start())
                self.update_publication_task=self.update_publication_worker.task
                self.update_publication_task.add_done_callback(lambda task:self._observed_worker_done(task,"update publication worker"))
            if self.details_publication_worker is not None:
                await self._startup_await(self.details_publication_worker.start())
                self.details_publication_task=self.details_publication_worker.task
                self.details_publication_task.add_done_callback(lambda task:self._observed_worker_done(task,"signal details publication worker"))
            comment_service=None
            if hasattr(sessions,"class_"):
                comment_delivery=SignalCommentPublicationService(transactions,TelegramPhotoPublisher(self.bot),self.initial_image_service,max_attempts=self.settings.update_send_max_attempts,retry_base_seconds=self.settings.update_send_retry_base_seconds)
                self.comment_publication_worker=SignalCommentPublicationWorker(comment_delivery,self.settings.update_publication_concurrency,self.settings.update_publication_sweep_seconds)
                await self._startup_await(self.comment_publication_worker.start());self.comment_publication_task=self.comment_publication_worker.task
                self.comment_publication_task.add_done_callback(lambda task:self._observed_worker_done(task,"signal comment publication worker"))
                comment_service=SignalCommentService(transactions,snapshot_provider,wake=self.comment_publication_worker.wake)
            await self._startup_await(self.market_worker.start())
            self.market_task = self.market_worker.task
            self.market_task.add_done_callback(lambda task:self._observed_worker_done(task,"market price worker"))
            if self.monitoring_worker is not None:
                await self._startup_await(self.monitoring_worker.start())
                self.monitoring_task=self.monitoring_worker.task
                self.monitoring_task.add_done_callback(lambda task:self._observed_worker_done(task,"monitoring worker"))
            if self.admin_alert_worker is not None:
                await self._startup_await(self.admin_alert_worker.start())
                self.admin_alert_task=self.admin_alert_worker.task
                self.admin_alert_task.add_done_callback(lambda task:self._observed_worker_done(task,"admin alert worker"))
            if self.notification_worker is not None:
                await self._startup_await(self.notification_worker.start())
                self.notification_task=self.notification_worker.task
                self.notification_task.add_done_callback(lambda task:self._observed_worker_done(task,"notification worker"))
            def wake_background():
                if self.publication_worker:self.publication_worker.wake()
                if self.update_publication_worker:self.update_publication_worker.wake()
                if self.details_publication_worker:self.details_publication_worker.wake()
                if self.monitoring_worker:self.monitoring_worker.wake()
                if self.admin_alert_worker:self.admin_alert_worker.wake()
            signal_service = SignalService(
                transactions, MarketReferencePriceProvider(market_probe, symbol_catalog, health_repository),
                publication_wake=wake_background,monitoring_wake=wake_background,
                details_publication_wake=wake_background)
            admin_signalist_service = AdminSignalistService(
                self.settings.application.main_admin_telegram_ids,
                service,
                transactions,
            )
            self.dispatcher = build_dispatcher(self.storage, idempotency, resolver,
                signalist_service=service, destination_service=destination_service,
                assignment_service=assignment_service,
                signal_service=signal_service, symbol_catalog=symbol_catalog, reporting_service=reporting_service, health_repository=health_repository,
                monitoring_service=monitoring_service,
                admin_signalist_service=admin_signalist_service,
                template_admin_service=template_admin_service,
                message_template_service=message_template_service,
                active_signal_service=active_signal_service,
                signal_comment_service=comment_service,
                destination_inspector=TelegramDestinationInspector(self.bot))
            self._checkpoint()
            await self._startup_await(self.bot.set_my_commands([
                BotCommand(command="start", description=texts.COMMAND_START),
                BotCommand(command="help", description=texts.COMMAND_HELP),
            ]))
            await self._startup_await(self.bot.delete_webhook(drop_pending_updates=False))
            self._checkpoint()

            self.state = RuntimeState.RUNNING
            self.logo_sync_task = asyncio.create_task(run_logo_sync(
                ToobitLogoSynchronizer(CoinLogoStore(self.settings.rendering.toobit_logo_root if self.settings.rendering else Path("var/coin_logos"))), self.logo_sync_stop))
            self.polling_task = asyncio.create_task(self.dispatcher.start_polling(
                self.bot, handle_signals=False, close_bot_session=False,
                handle_as_tasks=False, allowed_updates=["message", "callback_query"]))
            await self._supervise()
        except RuntimeShutdownRequested:
            pass
        except asyncio.CancelledError:
            raise
        except TelegramRuntimeError:
            raise
        except Exception as exc:
            raise TelegramRuntimeError("telegram runtime startup failed") from exc
        finally:
            await self.cleanup()

    async def _supervise(self) -> None:
        if self.polling_task is None or self.renewal_task is None:
            return
        shutdown_waiter = asyncio.create_task(self.shutdown_requested.wait())
        shutdown_was_requested = self.shutdown_requested.is_set()
        watched={self.polling_task,self.renewal_task,shutdown_waiter}
        if self.publication_task is not None:watched.add(self.publication_task)
        if self.monitoring_task is not None:watched.add(self.monitoring_task)
        if self.market_task is not None:watched.add(self.market_task)
        if self.notification_task is not None:watched.add(self.notification_task)
        if self.update_publication_task is not None:watched.add(self.update_publication_task)
        if self.admin_alert_task is not None:watched.add(self.admin_alert_task)
        if self.comment_publication_task is not None:watched.add(self.comment_publication_task)
        done, _ = await asyncio.wait(
            watched,
            return_when=asyncio.FIRST_COMPLETED,
        )
        fatal_reason: str | None = None
        if self.renewal_task in done:
            self._observe_task_failure(self.renewal_task, "poller renewal")
            if not self.renewal_stop.is_set():
                self.lease_lost.set()
                fatal_reason = "poller lease renewal stopped unexpectedly"
        if self.polling_task in done:
            self._observe_task_failure(self.polling_task, "telegram polling")
            if not shutdown_was_requested:
                fatal_reason = fatal_reason or "telegram polling stopped unexpectedly"
        if self.publication_task is not None and self.publication_task in done:
            self._observe_task_failure(self.publication_task,"publication worker")
            if self.state is RuntimeState.RUNNING:
                fatal_reason=fatal_reason or "publication worker stopped unexpectedly"
        if self.monitoring_task is not None and self.monitoring_task in done:
            self._observe_task_failure(self.monitoring_task,"monitoring worker")
            if self.state is RuntimeState.RUNNING:
                fatal_reason=fatal_reason or "monitoring worker stopped unexpectedly"
        if self.market_task is not None and self.market_task in done:
            self._observe_task_failure(self.market_task,"market price worker")
            if self.state is RuntimeState.RUNNING:
                fatal_reason=fatal_reason or "market price worker stopped unexpectedly"
        if self.notification_task is not None and self.notification_task in done:
            self._observe_task_failure(self.notification_task,"notification worker")
            if self.state is RuntimeState.RUNNING:
                fatal_reason=fatal_reason or "notification worker stopped unexpectedly"
        if self.admin_alert_task is not None and self.admin_alert_task in done:
            self._observe_task_failure(self.admin_alert_task,"admin alert worker")
            if self.state is RuntimeState.RUNNING:
                fatal_reason=fatal_reason or "admin alert worker stopped unexpectedly"
        if self.update_publication_task is not None and self.update_publication_task in done:
            self._observe_task_failure(self.update_publication_task,"update publication worker")
            if self.state is RuntimeState.RUNNING:
                fatal_reason=fatal_reason or "update publication worker stopped unexpectedly"
        if self.comment_publication_task is not None and self.comment_publication_task in done:
            self._observe_task_failure(self.comment_publication_task,"signal comment publication worker")
            if self.state is RuntimeState.RUNNING:fatal_reason=fatal_reason or "signal comment publication worker stopped unexpectedly"
        self.request_shutdown()
        shutdown_waiter.cancel()
        with suppress(asyncio.CancelledError):
            await shutdown_waiter
        if fatal_reason:
            raise TelegramRuntimeError(fatal_reason)

    async def _best_effort(self, action: Awaitable[object], component: str) -> bool:
        task = asyncio.ensure_future(action)
        try:
            await asyncio.wait_for(
                asyncio.shield(task), CLEANUP_OPERATION_TIMEOUT_SECONDS
            )
            return True
        except TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            LOGGER.error("cleanup failed component=%s category=TimeoutError", component)
            return False
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
            return False
        except Exception as exc:
            LOGGER.error("cleanup failed component=%s category=%s", component,
                         type(exc).__name__)
            return False

    async def cleanup(self) -> None:
        async with self._cleanup_lock:
            if self.state is RuntimeState.STOPPED:
                return
            self.state = RuntimeState.STOPPING
            self.shutdown_requested.set()

            self.logo_sync_stop.set()
            if self.logo_sync_task:
                await self._best_effort(self.logo_sync_task, "Toobit logo sync task")

            if self.dispatcher and self.polling_task and not self.polling_task.done():
                stopped = await self._best_effort(
                    self.dispatcher.stop_polling(),
                    "polling stop",
                )
                if not stopped and not self.polling_task.done():
                    self.polling_task.cancel()
            if self.polling_task:
                await self._best_effort(self.polling_task, "polling task")

            if self.monitoring_worker:
                await self._best_effort(self.monitoring_worker.stop(), "monitoring worker")
            if self.market_worker:
                await self._best_effort(self.market_worker.stop(), "market price worker")
            if self.notification_worker:
                await self._best_effort(self.notification_worker.stop(), "Signalist notification worker")
            if self.admin_alert_worker:
                await self._best_effort(self.admin_alert_worker.stop(), "admin alert worker")
            if self.update_publication_worker:
                await self._best_effort(self.update_publication_worker.close(), "update publication worker")
            if self.details_publication_worker:
                await self._best_effort(self.details_publication_worker.close(), "signal details publication worker")
            if self.cancellation_publication_worker:
                await self._best_effort(self.cancellation_publication_worker.close(), "signal cancellation publication worker")
            if self.comment_publication_worker:
                await self._best_effort(self.comment_publication_worker.close(), "signal comment publication worker")
            if self.publication_worker:
                await self._best_effort(self.publication_worker.close(), "publication worker")
            if self.initial_image_service:
                await self._best_effort(self.initial_image_service.aclose(), "initial image service")

            self.renewal_stop.set()
            if self.renewal_task:
                if self.renewal_task.done():
                    self._observe_task_failure(self.renewal_task, "poller renewal")
                else:
                    await self._best_effort(
                        self.renewal_task, "poller renewal task"
                    )

            if self.lease and not self._lease_released:
                if await self._best_effort(self.lease.release(), "poller lease release"):
                    self._lease_released = True

            if not self._redis_closed:
                if self.storage:
                    closed = await self._best_effort(self.storage.close(), "Redis storage")
                elif self.redis:
                    closed = await self._best_effort(
                        self.redis.aclose(close_connection_pool=True), "Redis client")
                else:
                    closed = True
                if closed:
                    self._redis_closed = True

            if self.bot and not self._bot_closed:
                if await self._best_effort(self.bot.session.close(), "Bot session"):
                    self._bot_closed = True

            if self.engine and not self._engine_disposed:
                if await self._best_effort(self.engine.dispose(), "database engine"):
                    self._engine_disposed = True

            if self.renderer_registration is not None:
                self.renderer_registration.close()
                self.renderer_registration = None

            self.state = RuntimeState.STOPPED
            self._cleanup_complete.set()

    async def _cleanup_orphan_outputs(self):
        root=self.settings.rendering.output_root.resolve()
        root.mkdir(parents=True,exist_ok=True)
        def clean():
            for pattern in ("initial-*.png", ".initial-*.png.*.tmp", "update-*.png", ".update-*.png.*.tmp"):
                for path in root.glob(pattern):
                    if path.is_file() and path.resolve().parent==root:path.unlink(missing_ok=True)
        await asyncio.to_thread(clean)


async def run() -> None:
    settings = load_telegram_runtime_settings()
    runtime = Runtime(settings)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, runtime.request_shutdown)
    await runtime.start()
