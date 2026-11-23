import asyncio
import unittest
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch
from aiogram.exceptions import TelegramRetryAfter
from PIL import Image as PILImage
from sqlalchemy import func,select,text

from chainura.application.limit_orders import LimitOrderService
from chainura.application.limit_publication import (LimitCancellationPublicationService,
    LimitPendingPublicationService)
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.application.initial_rendering import InitialImageService, InitialPayloadFactory
from chainura.application.monitoring import MonitoringService
from chainura.application.publication import (CancellationPublicationService, InitialPublicationService,
    SignalDetailsPublicationService, UpdatePublicationService)
from chainura.application.publication import DeliveryAmbiguous,DeliveryRateLimited,DeliveryRejected
from chainura.application.signal_service import (CreateLimitSignalCommand,CreatePendingSignalCommand,
    ReferencePriceResult,SignalService)
from chainura.application.signalist_notification import SignalistNotificationService
from chainura.application.active_signals import ActiveSignalService
from chainura.application.pagination import PageRequest
from chainura.domain.destination import Exchange
from chainura.domain.message_template import DestinationMessageKind
from chainura.domain.monitoring import MarketDecision, MonitorStage, NotificationStatus
from chainura.domain.publication import PublicationRequestStatus, PublicationStatus
from chainura.domain.signal import (PriceSourceType,SignalEntryType,SignalLifecycleStatus,
    SignalMode,TradeDirection)
from chainura.domain.signalist import Signalist
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import (DestinationPublicationRecord,DestinationTradeLockRecord,
    InitialPublicationDailySlotRecord,LimitCancellationPublicationRecord,LimitEntryTriggerRecord,LimitPendingPublicationRecord,
    SignalCancellationPublicationRecord,
    SignalDetailsPublicationRecord,SignalMonitorStateRecord,SignalPositionStateRecord,
    SignalRecord,SignalUpdateCandidateRecord,SignalistNotificationRecord,UpdatePublicationRequestRecord)
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.rendering.core import render_initial
from chainura.rendering.logos import CoinLogoStore
from chainura.rendering.models import EXCHANGE_IMAGE_SPECS
from chainura.rendering.pillow.compositor import _values
from chainura.rendering.pillow.text import DefaultTestFontProvider
from chainura.rendering.templates import TemplateAssetStore, TemplateService
from chainura.telegram.presentation.formatting import bidi_ascii
from chainura.telegram.runtime import _limit_trigger_markup
from integration_tests.support import create_safe_engine,migrate,require_safe_test_database

NOW=datetime(2026,8,18,8,tzinfo=timezone.utc)
def snapshot(value="100"):
    p=Decimal(value)
    return ReferencePriceResult(p,PriceSourceType.COMPOSITE,
        ((Exchange.OURBIT,p),(Exchange.TOOBIT,p)),True,"BTCUSDT",
        ((Exchange.OURBIT,"BTCUSDT"),(Exchange.TOOBIT,"BTCUSDT"),(Exchange.LBANK,"btc_usdt")))
class Prices:
    def __init__(self,value="100",result=None):self.value=value;self.result=result;self.calls=0
    async def get_reference_price(self,**kwargs):self.calls+=1;return self.result or snapshot(self.value)
class Publisher:
    def __init__(self):self.text=[];self.photos=[];self.update_photos=[]
    async def send_standalone_text(self,chat_id,body):self.text.append((chat_id,body));return 700+len(self.text)
    async def send_initial(self,chat_id,path,caption,reply_to_message_id=None):
        self.photos.append((chat_id,path,caption,reply_to_message_id));return 800+len(self.photos)
    async def send_text_update(self,chat_id,body,reply_to_message_id):
        self.text.append((chat_id,body,reply_to_message_id));return 900+len(self.text)
    async def send_details(self, chat_id, body, reply_to_message_id):
        return await self.send_text_update(chat_id, body, reply_to_message_id)
    async def send_update(self, chat_id, path, caption, reply_to_message_id):
        self.update_photos.append((chat_id, path, caption, reply_to_message_id))
        return 950 + len(self.update_photos)
class BlockingPublisher(Publisher):
    def __init__(self,kind="text",error=None):
        super().__init__();self.kind=kind;self.error=error
        self.started=asyncio.Event();self.release=asyncio.Event()
    async def _wait(self):
        self.started.set();await self.release.wait()
        if self.error:raise self.error
    async def send_standalone_text(self,chat_id,body):
        await self._wait();return await super().send_standalone_text(chat_id,body)
    async def send_initial(self,chat_id,path,caption,reply_to_message_id=None):
        await self._wait();return await super().send_initial(chat_id,path,caption,reply_to_message_id)
class Image:
    async def render_initial_signal(self, *args, trigger_prices=None):
        class Output:file_path=Path("/tmp/limit-test.png")
        return Output()
    async def render_update_signal(self, *args):
        return await self.render_initial_signal(*args)
    def cleanup(self,path):pass
class NotificationTransport:
    def __init__(self):self.calls=0;self.rows=[]
    async def send(self,telegram_id,body,kind,source_id):
        self.calls+=1;self.rows.append((telegram_id,body,kind,source_id))
        if self.calls==1:raise TelegramRetryAfter(method=Mock(),message="private",retry_after=12)
        return 999
class FailingPublisher(Publisher):
    def __init__(self,error):super().__init__();self.error=error
    async def send_standalone_text(self,chat_id,body):raise self.error

class Phase5LimitAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):cls.safe=require_safe_test_database();migrate("head",cls.safe)
    @classmethod
    def tearDownClass(cls):cls.safe.cleanup()
    async def asyncSetUp(self):
        self.engine=create_safe_engine(self.safe);self.sessions=create_session_factory(self.engine)
        async with self.engine.begin() as c:
            await c.execute(text("DELETE FROM signals"));await c.execute(text("DELETE FROM destinations"));await c.execute(text("DELETE FROM signalists"))
        async with self.sessions() as s,s.begin():self.actor=await SqlAlchemySignalistRepository(s).add(Signalist.create(554433,"Limit Owner"))
        self.tx=SqlAlchemySignalistTransactionFactory(self.sessions);self.clock_now=NOW
        async with self.engine.begin() as c:
            self.good=(await c.execute(text("INSERT INTO destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit,created_at,updated_at) VALUES('good',-1001,'channel','private','active',2,:n,:n) RETURNING id"),{"n":NOW})).scalar_one()
            self.blocked=(await c.execute(text("INSERT INTO destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit,created_at,updated_at) VALUES('blocked',-1002,'channel','public','active',2,:n,:n) RETURNING id"),{"n":NOW})).scalar_one()
            for did in (self.good,self.blocked):
                await c.execute(text("INSERT INTO destination_exchange_priorities(destination_id,exchange,position) VALUES(:d,'ourbit',1)"),{"d":did})
                await c.execute(text("INSERT INTO destination_signalists(destination_id,signalist_id,created_at) VALUES(:d,:s,:n)"),{"d":did,"s":self.actor.id,"n":NOW})
        self.prices=Prices();self.signal_service=SignalService(self.tx,self.prices,lambda:self.clock_now)
    async def asyncTearDown(self):await self.engine.dispose()
    def command(self,direction=TradeDirection.LONG,token="limit-one"):
        entry=Decimal("90") if direction is TradeDirection.LONG else Decimal("110")
        tps=(Decimal("95"),Decimal("105")) if direction is TradeDirection.LONG else (Decimal("105"),Decimal("95"))
        stop=Decimal("85") if direction is TradeDirection.LONG else Decimal("115")
        return CreateLimitSignalCommand(self.actor.id,token,SignalMode.COMPOSITE,"BTCUSDT","BTC","USDT",None,None,direction,entry,10,tps,stop)
    async def create_blocking_market(self):
        p=snapshot();cmd=CreatePendingSignalCommand(self.actor.id,"market-owner",SignalMode.COMPOSITE,"BTCUSDT","BTC","USDT",None,None,TradeDirection.LONG,10,p)
        signal=await self.signal_service.create_pending(cmd)
        async with self.sessions() as s,s.begin():s.add(DestinationTradeLockRecord(destination_id=self.blocked,canonical_symbol="BTCUSDT",signal_id=signal.id,acquired_at=NOW,updated_at=NOW))
        return signal

    async def only_good_destination(self):
        async with self.engine.begin() as c:
            await c.execute(text("UPDATE destinations SET status='inactive' WHERE id=:d"),{"d":self.blocked})

    async def legacy_limit(self, command):
        signal = await self.signal_service.create_limit(command)
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE signals SET auto_activate_limit=0 WHERE id=:id"),
                                     {"id": signal.id})
        return signal

    async def test_long_production_style_lifecycle_and_retry(self):
        await self.create_blocking_market();signal=await self.signal_service.create_limit(self.command())
        duplicate=await self.signal_service.create_limit(self.command())
        self.assertEqual(signal.id,duplicate.id);self.assertIs(signal.entry_type,SignalEntryType.LIMIT)
        self.assertIs(signal.status,SignalLifecycleStatus.PENDING_ENTRY);self.assertEqual(self.prices.calls,2)
        publisher=Publisher();templates=DestinationMessageTemplateService(self.tx)
        await templates.set_global(DestinationMessageKind.INITIAL_PHOTO_CAPTION, "ورود {entry}\nآخرین قیمت {last_price}\nبازده {pnl}")
        pending=LimitPendingPublicationService(self.tx,publisher,templates,lambda:self.clock_now)
        await pending.plan_pending()
        async with self.sessions() as s:
            rows=(await s.scalars(select(LimitPendingPublicationRecord).where(LimitPendingPublicationRecord.signal_id==signal.id).order_by(LimitPendingPublicationRecord.destination_id))).all()
            self.assertEqual({r.destination_id:r.status for r in rows},{self.good:PublicationStatus.PENDING,self.blocked:PublicationStatus.SUPERSEDED})
            self.assertEqual(await s.scalar(select(func.count()).select_from(InitialPublicationDailySlotRecord).where(InitialPublicationDailySlotRecord.limit_publication_id.is_not(None))),1)
        ids=await pending.pending_ids();self.assertEqual(len(ids),1);await pending.process(ids[0])
        async with self.sessions() as s:
            admitted=await s.scalar(select(LimitPendingPublicationRecord).where(LimitPendingPublicationRecord.signal_id==signal.id,LimitPendingPublicationRecord.destination_id==self.good))
            self.assertIs(admitted.status,PublicationStatus.SENT);self.assertEqual(admitted.telegram_message_id,701)
        decision = MarketDecision(Decimal("89"),
                                  ((Exchange.OURBIT, Decimal("88")), (Exchange.TOOBIT, Decimal("90"))),
                                  PriceSourceType.COMPOSITE, NOW)
        market = Mock(snapshot=AsyncMock(return_value={signal.id: decision}))
        limit=LimitOrderService(self.sessions,market,clock=lambda:self.clock_now)
        self.assertFalse(await limit.observe(signal.id,Decimal("91"),PriceSourceType.COMPOSITE,snapshot("91").source_prices,NOW))
        self.assertEqual(sum(await asyncio.gather(limit.run_cycle(), limit.run_cycle())), 1)
        market_calls = market.snapshot.await_count
        monitoring = MonitoringService(self.sessions, market, clock=lambda:self.clock_now)
        self.assertEqual([work.signal_id for work in await monitoring.load_work(100)], [signal.id])
        async with self.sessions() as s:
            trigger = await s.get(LimitEntryTriggerRecord, signal.id)
            self.assertEqual((trigger.requested_entry, trigger.observed_reference_price, trigger.occurred_at),
                             (Decimal("90"), Decimal("89"), NOW))
            note=await s.scalar(select(SignalistNotificationRecord).where(SignalistNotificationRecord.limit_trigger_signal_id==signal.id))
            self.assertIs(note.status,NotificationStatus.PENDING)
        transport=NotificationTransport();notifications=SignalistNotificationService(self.sessions,transport,max_attempts=1,retry_base_seconds=1,clock=lambda:self.clock_now)
        await notifications.deliver(note.id)
        async with self.sessions() as s:
            note=await s.get(SignalistNotificationRecord,note.id);self.assertIs(note.status,NotificationStatus.RETRY_WAIT);self.assertEqual(note.last_error_category,"rate_limited");self.assertEqual(note.retry_at,NOW+timedelta(seconds=12))
        self.clock_now+=timedelta(seconds=12)
        await asyncio.gather(notifications.deliver(note.id), notifications.deliver(note.id))
        self.assertEqual(transport.calls, 2)
        self.assertIn("به‌صورت خودکار فعال شد", transport.rows[-1][1])
        self.assertIsNone(await _limit_trigger_markup(self.sessions, signal.id))
        async with self.sessions() as s:
            db=await s.get(SignalRecord,signal.id);self.assertIs(db.status,SignalLifecycleStatus.ACTIVE);self.assertEqual(db.entry_price,Decimal("90"))
            self.assertIsNotNone(await s.get(SignalPositionStateRecord,signal.id));self.assertIsNotNone(await s.get(SignalMonitorStateRecord,signal.id))
            activations=(await s.scalars(select(DestinationPublicationRecord).where(DestinationPublicationRecord.signal_id==signal.id))).all()
            self.assertEqual(len(activations),1);self.assertEqual(activations[0].reply_to_message_id,701)
            self.assertEqual(await s.scalar(select(func.count()).select_from(InitialPublicationDailySlotRecord).where(InitialPublicationDailySlotRecord.limit_publication_id.is_not(None))),1)
        images = Image()
        images.render_initial_signal = AsyncMock(wraps=images.render_initial_signal)
        initial=InitialPublicationService(self.tx,images,publisher,lambda:self.clock_now,message_templates=templates)
        await initial.recover()
        self.assertEqual(await LimitOrderService(self.sessions, market).load_work(), ())
        await initial.process(activations[0].id)
        await initial.process(activations[0].id)
        self.assertEqual(len(publisher.photos), 1)
        self.assertEqual(publisher.photos[0][3],701)
        self.assertEqual(publisher.photos[0][2],
                         f"ورود {bidi_ascii('90')}\nآخرین قیمت {bidi_ascii('88')}\nبازده {bidi_ascii('-22.22%')}")
        self.assertEqual(self.prices.calls, 2)
        self.assertEqual(market.snapshot.await_count, market_calls)
        args, kwargs = images.render_initial_signal.await_args
        initial_signal, exchange, destination, rendered_at = args
        factory = InitialPayloadFactory(Mock())
        payload = factory.build(initial_signal, exchange, rendered_at, **kwargs)
        self.assertEqual((payload.entry_price, payload.reference_price, payload.display_price),
                         (Decimal("90"), Decimal("89"), Decimal("88")))
        self.assertEqual(_values(payload)["pnl"], "-22.22%")
        self.assertEqual((exchange, destination), (Exchange.OURBIT, self.good))
        details = SignalDetailsPublicationService(self.tx, publisher, lambda:self.clock_now,
                                                  message_templates=templates)
        for item_id in await details.pending_ids():
            await details.process(item_id)
        self.assertEqual(publisher.text[-1][2], 801)
        async with self.sessions() as s:
            activation=await s.get(DestinationPublicationRecord,activations[0].id);self.assertIs(activation.status,PublicationStatus.SENT)
            self.assertIsNotNone(await s.scalar(select(SignalDetailsPublicationRecord).where(SignalDetailsPublicationRecord.signal_id==signal.id)))
            self.assertEqual(await s.scalar(select(func.count()).select_from(DestinationTradeLockRecord).where(DestinationTradeLockRecord.signal_id==signal.id)),1)

    async def test_activated_limit_full_close_before_tp_uses_fresh_price_and_pending_reply(self):
        from chainura.application.monitoring import MonitoringService
        from chainura.application.publication import PositionActionPublicationService
        from chainura.domain.monitoring import MarketDecision,MonitorStage
        from chainura.persistence.models import SignalPositionActionPublicationRecord
        await self.only_good_destination()
        signal=await self.legacy_limit(self.command())
        publisher=Publisher();templates=DestinationMessageTemplateService(self.tx)
        pending=LimitPendingPublicationService(self.tx,publisher,templates,lambda:self.clock_now)
        await pending.plan_pending()
        for pid in await pending.pending_ids():await pending.process(pid)
        class Fresh:
            async def fresh_snapshot(inner,work):
                return {signal.id:MarketDecision(Decimal('92'),snapshot('92').source_prices,PriceSourceType.COMPOSITE,self.clock_now)}
        monitoring=MonitoringService(self.sessions,Fresh(),clock=lambda:self.clock_now)
        with self.assertRaises(ValueError):await monitoring.manual_close_version(signal.id,self.actor.id)
        limit=LimitOrderService(self.sessions,clock=lambda:self.clock_now)
        await limit.observe(signal.id,Decimal('90'),PriceSourceType.COMPOSITE,snapshot('90').source_prices,self.clock_now)
        with self.assertRaises(ValueError):await monitoring.manual_close_version(signal.id,self.actor.id)
        await limit.activate(signal.id,self.actor.id)
        version=await monitoring.manual_close_version(signal.id,self.actor.id)
        await monitoring.manual_full_close(signal.id,self.actor.id,version)
        async with self.sessions() as s:
            position=await s.get(SignalPositionStateRecord,signal.id)
            self.assertTrue(position.is_closed)
            self.assertEqual(position.realized_weighted_roi,Decimal('2')/Decimal('90')*100*10)
            self.assertIs((await s.get(SignalMonitorStateRecord,signal.id)).stage,MonitorStage.COMPLETED_FULL_CLOSE)
            self.assertEqual(await s.scalar(select(func.count()).select_from(DestinationTradeLockRecord).where(DestinationTradeLockRecord.signal_id==signal.id)),0)
        # Activation photo is still queued: terminal text must reply to known Pending,
        # and initial delivery must neither reacquire its lock nor create a standalone.
        initial=InitialPublicationService(self.tx,Image(),publisher,lambda:self.clock_now,message_templates=templates)
        async with self.sessions() as s:activation=await s.scalar(select(DestinationPublicationRecord).where(DestinationPublicationRecord.signal_id==signal.id))
        await initial.process(activation.id);self.assertEqual(publisher.photos,[])
        delivery=PositionActionPublicationService(self.tx,publisher,templates,lambda:self.clock_now)
        for pid in await delivery.pending_ids():await delivery.process(pid)
        self.assertEqual(publisher.text[-1][2],701)

    async def test_short_threshold_and_moved_price_does_not_revert(self):
        await self.only_good_destination()
        signal=await self.legacy_limit(self.command(TradeDirection.SHORT,"short"))
        publisher = Publisher()
        templates = DestinationMessageTemplateService(self.tx)
        await templates.set_global(DestinationMessageKind.INITIAL_PHOTO_CAPTION, "{entry} {last_price} {pnl}")
        pending = LimitPendingPublicationService(self.tx, publisher, templates, lambda: self.clock_now)
        await pending.plan_pending()
        pending_id, = await pending.pending_ids()
        await pending.process(pending_id)
        service=LimitOrderService(self.sessions,clock=lambda:self.clock_now)
        self.assertFalse(await service.observe(signal.id,Decimal("109"),PriceSourceType.COMPOSITE,snapshot("109").source_prices,NOW))
        trigger_quotes = ((Exchange.OURBIT, Decimal("109")), (Exchange.TOOBIT, Decimal("111")))
        self.assertTrue(await service.observe(signal.id, Decimal("110"), PriceSourceType.COMPOSITE, trigger_quotes, NOW))
        self.assertFalse(await service.observe(signal.id,Decimal("100"),PriceSourceType.COMPOSITE,snapshot("100").source_prices,NOW))
        async with self.sessions() as s:self.assertIs((await s.get(SignalRecord,signal.id)).status,SignalLifecycleStatus.ENTRY_TRIGGERED)
        transport = Mock(send=AsyncMock(return_value=33))
        notifications = SignalistNotificationService(self.sessions, transport)
        await notifications.recover()
        await notifications.deliver((await notifications.pending())[0])
        self.assertIn("صریحاً فعال کنید", transport.send.await_args.args[1])
        self.assertIsNotNone(await _limit_trigger_markup(self.sessions, signal.id))
        self.clock_now += timedelta(days=3)
        self.prices.value = "120"
        self.assertTrue(await service.activate(signal.id,self.actor.id))
        self.assertTrue(await service.activate(signal.id,self.actor.id))
        images = Image()
        images.render_initial_signal = AsyncMock(wraps=images.render_initial_signal)
        initial = InitialPublicationService(self.tx, images, publisher, lambda: self.clock_now,
                                            message_templates=templates)
        activation_id, = await initial.pending_ids()
        await initial.process(activation_id)
        args, kwargs = images.render_initial_signal.await_args
        rendered_signal, exchange, _, rendered_at = args
        payload = InitialPayloadFactory(Mock()).build(rendered_signal, exchange, rendered_at, **kwargs)
        self.assertEqual((payload.entry_price, payload.display_price), (Decimal("110"), Decimal("109")))
        self.assertEqual(_values(payload)["pnl"], "+9.09%")
        self.assertEqual(publisher.photos[0][2:], (f"{bidi_ascii('110')} {bidi_ascii('109')} {bidi_ascii('9.09%')}", 701))
        self.assertEqual(self.prices.calls, 1)

    async def test_multi_exchange_limit_initial_retry_resolves_each_destination_template(self):
        async with self.engine.begin() as connection:
            await connection.execute(text("UPDATE destination_exchange_priorities SET exchange='toobit' WHERE destination_id=:d"),
                                     {"d": self.blocked})
        self.prices.result = replace(snapshot(),
            source_prices=((Exchange.OURBIT, Decimal("100")), (Exchange.TOOBIT, Decimal("101"))),
            native_symbols=((Exchange.OURBIT, "BTC_USDT"), (Exchange.TOOBIT, "BTC-SWAP-USDT"),
                            (Exchange.LBANK, "btc_usdt")))
        signal = await self.signal_service.create_limit(self.command())
        publisher = Publisher()
        templates = DestinationMessageTemplateService(self.tx)
        kind = DestinationMessageKind.INITIAL_PHOTO_CAPTION
        await templates.set_global(kind, "{exchange} {entry} {last_price} {pnl}")
        await templates.set_destination(kind, self.blocked, "{symbol} {exchange} {entry} {last_price} {pnl}")
        pending = LimitPendingPublicationService(self.tx, publisher, templates, lambda: self.clock_now)
        await pending.plan_pending()
        for item_id in await pending.pending_ids():
            await pending.process(item_id)
        orders = LimitOrderService(self.sessions, clock=lambda: self.clock_now)
        trigger_quotes = ((Exchange.OURBIT, Decimal("89")), (Exchange.TOOBIT, Decimal("91")))
        self.assertTrue(await orders.observe(signal.id, Decimal("90"), PriceSourceType.COMPOSITE, trigger_quotes, NOW))

        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = TemplateAssetStore(root / "templates")
            image_templates = TemplateService(self.tx, store)
            for exchange, color, destination in ((Exchange.OURBIT, "black", None),
                                                 (Exchange.TOOBIT, "blue", None),
                                                 (Exchange.TOOBIT, "green", self.blocked)):
                spec = EXCHANGE_IMAGE_SPECS[exchange]
                png = root / "template.png"
                PILImage.new("RGB", (spec.width, spec.height), color).save(png)
                await image_templates.install(exchange, png.read_bytes(), destination_id=destination)
            images = InitialImageService(InitialPayloadFactory(CoinLogoStore(root / "logos")),
                                         image_templates, root / "out", 1)
            self.addAsyncCleanup(images.aclose)
            initial = InitialPublicationService(self.tx, images, publisher, lambda: self.clock_now,
                                                message_templates=templates)
            async with self.sessions() as session:
                deliveries = {row.selected_exchange: row.id for row in
                              (await session.scalars(select(DestinationPublicationRecord))).all()}
            toobit_id = deliveries[Exchange.TOOBIT]
            async with self.tx() as tx:
                self.assertIsNotNone(await tx.publications.claim(toobit_id, self.clock_now))
                await tx.commit()
            await initial.recover()  # Reclaim the interrupted render.

            def render(payload, template, output):
                return render_initial(payload, template, output, DefaultTestFontProvider())

            with patch("chainura.application.initial_rendering.render_signal_image", side_effect=render) as renderer:
                await initial.process(deliveries[Exchange.OURBIT])
                with patch.object(publisher, "send_initial", new=AsyncMock(side_effect=DeliveryRateLimited(3))) as retry:
                    await initial.process(toobit_id)
                self.clock_now += timedelta(seconds=3)
                await initial.recover()
                await initial.process(toobit_id)

            self.assertEqual(retry.await_args.args[2:], publisher.photos[-1][2:])
            entry = bidi_ascii("90")
            self.assertEqual(publisher.photos[0][2], f"Ourbit {entry} {bidi_ascii('89')} {bidi_ascii('-11.11%')}")
            self.assertEqual(publisher.photos[1][2],
                             f"{bidi_ascii('BTCUSDT')} Toobit {entry} {bidi_ascii('91')} {bidi_ascii('11.11%')}")
            self.assertEqual(renderer.call_count, 3)
            ourbit = (Exchange.OURBIT, "BTC_USDT", None, "89", "-11.11%")
            toobit = (Exchange.TOOBIT, "BTC-SWAP-USDT", self.blocked, "91", "+11.11%")
            for call, (exchange, native_symbol, destination, quote, pnl) in zip(renderer.call_args_list, (ourbit, toobit, toobit)):
                payload, template, _ = call.args
                self.assertIs(payload.exchange, exchange)
                self.assertEqual((payload.canonical_symbol, payload.native_symbol), ("BTCUSDT", native_symbol))
                self.assertEqual((payload.entry_price, payload.display_price), (Decimal("90"), Decimal(quote)))
                self.assertEqual((_values(payload)["price"], _values(payload)["pnl"]), (quote, pnl))
                resolved = await image_templates.resolve(exchange, destination)
                self.assertEqual(template, resolved.path)
                self.assertEqual(resolved.is_destination_specific, destination is not None)
            self.assertEqual(list(images.output_root.iterdir()), [])
        self.assertEqual(self.prices.calls, 1)

    async def test_limit_initial_fails_before_send_if_destination_trigger_quote_is_missing(self):
        await self.only_good_destination()
        signal = await self.signal_service.create_limit(self.command())
        publisher = Publisher()
        templates = DestinationMessageTemplateService(self.tx)
        pending = LimitPendingPublicationService(self.tx, publisher, templates, lambda: self.clock_now)
        await pending.plan_pending()
        pending_id, = await pending.pending_ids()
        await pending.process(pending_id)
        trigger_quotes = ((Exchange.TOOBIT, Decimal("88")), (Exchange.LBANK, Decimal("90")))
        orders = LimitOrderService(self.sessions, clock=lambda: self.clock_now)
        self.assertTrue(await orders.observe(signal.id, Decimal("89"), PriceSourceType.COMPOSITE, trigger_quotes, NOW))

        images = Mock(render_initial_signal=AsyncMock())
        initial = InitialPublicationService(self.tx, images, publisher, lambda: self.clock_now,
                                            message_templates=templates)
        delivery_id, = await initial.pending_ids()
        await initial.process(delivery_id)
        await initial.recover()
        self.assertEqual(await initial.pending_ids(), ())
        images.render_initial_signal.assert_not_awaited()
        self.assertEqual(publisher.photos, [])
        self.assertEqual(self.prices.calls, 1)
        async with self.sessions() as session:
            delivery = await session.get(DestinationPublicationRecord, delivery_id)
            self.assertEqual((delivery.status, delivery.last_error_category),
                             (PublicationStatus.FAILED, "limit_trigger_price_missing"))
            self.assertIs((await session.get(LimitPendingPublicationRecord, pending_id)).status, PublicationStatus.SENT)
            self.assertIs((await session.get(SignalRecord, signal.id)).status, SignalLifecycleStatus.ACTIVE)

    async def test_weex_single_exchange_limit_admission_trigger_and_activation(self):
        await self.only_good_destination()
        async with self.engine.begin() as c:
            await c.execute(text("DELETE FROM destination_exchange_priorities WHERE destination_id=:d"),{"d":self.good})
            await c.execute(text("INSERT INTO destination_exchange_priorities(destination_id,exchange,position) VALUES(:d,'weex',1)"),{"d":self.good})
        result=ReferencePriceResult(Decimal("100"),PriceSourceType.WEEX_ONLY,
            ((Exchange.WEEX,Decimal("100")),),True,"BTCUSDT",((Exchange.WEEX,"BTCUSDT"),))
        service=SignalService(self.tx,Prices(result=result),lambda:self.clock_now)
        command=CreateLimitSignalCommand(self.actor.id,"weex-limit",SignalMode.SINGLE_EXCHANGE,
            "BTCUSDT","BTC","USDT",Exchange.WEEX,"BTCUSDT",TradeDirection.LONG,
            Decimal("90"),10,(Decimal("95"),Decimal("105")),Decimal("85"))
        signal=await service.create_limit(command)
        self.assertIs(signal.price_source_type,PriceSourceType.WEEX_ONLY)
        self.assertEqual(signal.exchange_symbols,((Exchange.WEEX,"BTCUSDT"),))
        publisher=Publisher();pending=LimitPendingPublicationService(self.tx,publisher,
            DestinationMessageTemplateService(self.tx),lambda:self.clock_now)
        await pending.plan_pending()
        async with self.sessions() as s:
            row=await s.scalar(select(LimitPendingPublicationRecord).where(
                LimitPendingPublicationRecord.signal_id==signal.id))
            self.assertIs(row.selected_exchange,Exchange.WEEX)
        await pending.process(row.id)
        limits=LimitOrderService(self.sessions,clock=lambda:self.clock_now)
        self.assertFalse(await limits.observe(signal.id,Decimal("91"),PriceSourceType.WEEX_ONLY,
            ((Exchange.WEEX,Decimal("91")),),NOW))
        self.assertTrue(await limits.observe(signal.id,Decimal("90"),PriceSourceType.WEEX_ONLY,
            ((Exchange.WEEX,Decimal("90")),),NOW))
        self.assertFalse(await limits.observe(signal.id,Decimal("89"),PriceSourceType.WEEX_ONLY,
            ((Exchange.WEEX,Decimal("89")),),NOW))
        async with self.sessions() as s:
            self.assertEqual(await s.scalar(select(func.count()).select_from(LimitEntryTriggerRecord)
                .where(LimitEntryTriggerRecord.signal_id==signal.id)),1)
            activation=await s.scalar(select(DestinationPublicationRecord).where(
                DestinationPublicationRecord.signal_id==signal.id))
            self.assertIs(activation.selected_exchange,Exchange.WEEX)
            self.assertIs((await s.get(SignalRecord,signal.id)).status,SignalLifecycleStatus.ACTIVE)

    async def test_triggered_active_signal_fallback_and_atomic_cancellation(self):
        signal=await self.legacy_limit(self.command(token="cancel-limit"))
        publisher=Publisher();pending=LimitPendingPublicationService(self.tx,publisher,
            DestinationMessageTemplateService(self.tx),lambda:self.clock_now)
        await pending.plan_pending()
        service=LimitOrderService(self.sessions,clock=lambda:self.clock_now)
        self.assertTrue(await service.observe(signal.id,Decimal("89"),PriceSourceType.COMPOSITE,
            snapshot("89").source_prices,NOW))
        active=ActiveSignalService(self.tx,lambda:self.clock_now)
        page=await active.list_open(self.actor.id,PageRequest(1));item=next(x for x in page.items if x.id==signal.id)
        self.assertIs(item.status,SignalLifecycleStatus.ENTRY_TRIGGERED);self.assertEqual(item.trigger_price,Decimal("89"))
        result=await active.cancel_signal(signal.id,self.actor.id);self.assertTrue(result.cancelled)
        async with self.sessions() as s:
            self.assertIs((await s.get(SignalRecord,signal.id)).status,SignalLifecycleStatus.CANCELLED)
            note=await s.scalar(select(SignalistNotificationRecord).where(
                SignalistNotificationRecord.limit_trigger_signal_id==signal.id))
            self.assertIs(note.status,NotificationStatus.SUPERSEDED)
            self.assertEqual(await s.scalar(select(func.count()).select_from(
                InitialPublicationDailySlotRecord).where(
                InitialPublicationDailySlotRecord.limit_publication_id.is_not(None))),0)
            self.assertEqual(await s.scalar(select(func.count()).select_from(
                DestinationTradeLockRecord).where(DestinationTradeLockRecord.signal_id==signal.id)),0)

    async def test_market_and_limit_share_one_race_safe_daily_capacity(self):
        async with self.engine.begin() as c:
            await c.execute(text("UPDATE destinations SET daily_limit=1 WHERE id=:d"),{"d":self.good})
            await c.execute(text("UPDATE destinations SET status='inactive' WHERE id=:d"),{"d":self.blocked})
        eth=replace(snapshot(),canonical_symbol="ETHUSDT",native_symbols=((Exchange.OURBIT,"ETHUSDT"),(Exchange.TOOBIT,"ETHUSDT"),(Exchange.LBANK,"eth_usdt")))
        market_service=SignalService(self.tx,Prices(result=eth),lambda:self.clock_now)
        market=await market_service.create_pending(CreatePendingSignalCommand(self.actor.id,
            "capacity-market",SignalMode.COMPOSITE,"ETHUSDT","ETH","USDT",None,None,
            TradeDirection.LONG,10,eth))
        async with self.tx() as tx:
            request=await tx._session.scalar(select(__import__('chainura.persistence.models',fromlist=['InitialPublicationRequestRecord']).InitialPublicationRequestRecord.id).where(
                __import__('chainura.persistence.models',fromlist=['InitialPublicationRequestRecord']).InitialPublicationRequestRecord.signal_id==market.id))
            await tx.publications.plan(request,self.clock_now);publication=await tx._session.scalar(select(
                DestinationPublicationRecord).where(DestinationPublicationRecord.signal_id==market.id,
                DestinationPublicationRecord.destination_id==self.good))
            self.assertEqual(await tx.publications.claim_capacity(publication.id,self.good,
                self.clock_now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Tehran')).date(),1,self.clock_now),1)
            await tx.commit()
        limit=await self.signal_service.create_limit(self.command(token="capacity-limit"))
        service=LimitPendingPublicationService(self.tx,Publisher(),DestinationMessageTemplateService(self.tx),lambda:self.clock_now)
        await service.plan_pending()
        async with self.sessions() as s:
            row=await s.scalar(select(LimitPendingPublicationRecord).where(
                LimitPendingPublicationRecord.signal_id==limit.id,
                LimitPendingPublicationRecord.destination_id==self.good))
            self.assertIs(row.status,PublicationStatus.SKIPPED_DAILY_LIMIT)
            self.assertEqual(await s.scalar(select(func.count()).select_from(
                InitialPublicationDailySlotRecord).where(
                InitialPublicationDailySlotRecord.destination_id==self.good)),1)
            self.assertIsNone(await s.scalar(select(DestinationTradeLockRecord.signal_id).where(
                DestinationTradeLockRecord.destination_id==self.good,
                DestinationTradeLockRecord.canonical_symbol=="BTCUSDT")))

    async def test_pending_delivery_retry_ambiguity_rejection_and_template_override(self):
        signal=await self.signal_service.create_limit(self.command(token="delivery-limit"))
        templates=DestinationMessageTemplateService(self.tx)
        await templates.set_destination(__import__('chainura.domain.message_template',fromlist=['DestinationMessageKind']).DestinationMessageKind.LIMIT_PENDING,self.good,"سفارش اختصاصی {symbol}")
        service=LimitPendingPublicationService(self.tx,FailingPublisher(DeliveryRateLimited(9)),
            templates,lambda:self.clock_now,retry_base_seconds=1)
        await service.plan_pending()
        async with self.sessions() as s:
            good=await s.scalar(select(LimitPendingPublicationRecord).where(
                LimitPendingPublicationRecord.signal_id==signal.id,
                LimitPendingPublicationRecord.destination_id==self.good))
            other=await s.scalar(select(LimitPendingPublicationRecord).where(
                LimitPendingPublicationRecord.signal_id==signal.id,
                LimitPendingPublicationRecord.destination_id==self.blocked))
        await service.process(good.id)
        async with self.sessions() as s:
            row=await s.get(LimitPendingPublicationRecord,good.id)
            self.assertIs(row.status,PublicationStatus.RETRY_WAIT);self.assertEqual(row.last_error_category,"rate_limited")
            self.assertEqual(row.retry_at,NOW+timedelta(seconds=9))
        self.clock_now+=timedelta(seconds=9);service.publisher=FailingPublisher(DeliveryAmbiguous("network"));await service.process(good.id)
        await service.recover()
        async with self.sessions() as s:self.assertIs((await s.get(LimitPendingPublicationRecord,good.id)).status,PublicationStatus.DELIVERY_UNKNOWN)
        service.publisher=FailingPublisher(DeliveryRejected("chat_not_found"));await service.process(other.id)
        async with self.sessions() as s:
            row=await s.get(LimitPendingPublicationRecord,other.id);self.assertIs(row.status,PublicationStatus.FAILED)
            self.assertEqual(row.last_error_category,"chat_not_found")
            self.assertIsNone(await s.scalar(select(InitialPublicationDailySlotRecord.id).where(
                InitialPublicationDailySlotRecord.limit_publication_id==other.id)))

    async def test_pending_send_cancel_then_sent_queues_one_exact_cancellation(self):
        await self.only_good_destination();signal=await self.signal_service.create_limit(
            self.command(token="pending-cancel-race"))
        publisher=BlockingPublisher();templates=DestinationMessageTemplateService(self.tx)
        wakes=[];service=LimitPendingPublicationService(self.tx,publisher,templates,
            lambda:self.clock_now,cancellation_wake=lambda:wakes.append(True))
        await service.plan_pending();publication_id=(await service.pending_ids())[0]
        sending=asyncio.create_task(service.process(publication_id));await publisher.started.wait()
        self.assertTrue((await ActiveSignalService(self.tx,lambda:self.clock_now).cancel_signal(
            signal.id,self.actor.id)).cancelled)
        async with self.sessions() as s:
            self.assertEqual(await s.scalar(select(func.count()).select_from(
                LimitCancellationPublicationRecord)),0)
        publisher.release.set();await sending
        async with self.sessions() as s:
            rows=(await s.scalars(select(LimitCancellationPublicationRecord))).all()
            self.assertEqual(len(rows),1);row=rows[0]
            self.assertEqual((row.signal_id,row.destination_id,row.pending_publication_id,
                row.reply_to_message_id),(signal.id,self.good,publication_id,701))
        self.assertEqual(wakes,[True])
        await service._transition(publication_id,PublicationStatus.SENDING,
            PublicationStatus.SENT,message_id=701)
        async with self.sessions() as s:
            self.assertEqual(await s.scalar(select(func.count()).select_from(
                LimitCancellationPublicationRecord)),1)
        cancellation=LimitCancellationPublicationService(self.tx,publisher,templates,
            lambda:self.clock_now);await cancellation.process(row.id)
        self.assertEqual(publisher.text[-1][2],701)
        self.assertIn("شناسه معامله",publisher.text[-1][1])
        self.assertIn(f"TR-{signal.id:06d}",publisher.text[-1][1])

    async def test_activation_send_cancel_then_sent_queues_one_normal_cancellation(self):
        await self.only_good_destination();signal=await self.signal_service.create_limit(
            self.command(token="activation-cancel-race"))
        publisher=Publisher();templates=DestinationMessageTemplateService(self.tx)
        pending=LimitPendingPublicationService(self.tx,publisher,templates,lambda:self.clock_now)
        await pending.plan_pending();await pending.process((await pending.pending_ids())[0])
        orders=LimitOrderService(self.sessions,clock=lambda:self.clock_now)
        await orders.observe(signal.id,Decimal("90"),PriceSourceType.COMPOSITE,
            snapshot("90").source_prices,NOW);await orders.activate(signal.id,self.actor.id)
        async with self.sessions() as s:
            activation=await s.scalar(select(DestinationPublicationRecord).where(
                DestinationPublicationRecord.signal_id==signal.id))
        blocking=BlockingPublisher(kind="photo");wakes=[]
        initial=InitialPublicationService(self.tx,Image(),blocking,lambda:self.clock_now,
            message_templates=templates,cancellation_wake=lambda:wakes.append(True))
        sending=asyncio.create_task(initial.process(activation.id));await blocking.started.wait()
        self.assertTrue((await ActiveSignalService(self.tx,lambda:self.clock_now).cancel_signal(
            signal.id,self.actor.id)).cancelled)
        blocking.release.set();await sending
        async with self.sessions() as s:
            rows=(await s.scalars(select(SignalCancellationPublicationRecord))).all()
            self.assertEqual(len(rows),1);cancellation_row=rows[0]
        self.assertEqual(wakes,[True])
        normal=CancellationPublicationService(self.tx,blocking,lambda:self.clock_now,
            message_templates=templates);await normal.process(cancellation_row.id)
        self.assertEqual(blocking.text[-1][2],801)
        await normal.pending_ids()
        async with self.sessions() as s:
            self.assertEqual(await s.scalar(select(func.count()).select_from(
                SignalCancellationPublicationRecord)),1)

    async def test_cancelled_ambiguous_pending_send_has_no_guessed_compensation(self):
        await self.only_good_destination();signal=await self.signal_service.create_limit(
            self.command(token="ambiguous-cancel-race"))
        publisher=BlockingPublisher(error=DeliveryAmbiguous("network"))
        service=LimitPendingPublicationService(self.tx,publisher,
            DestinationMessageTemplateService(self.tx),lambda:self.clock_now)
        await service.plan_pending();publication_id=(await service.pending_ids())[0]
        sending=asyncio.create_task(service.process(publication_id));await publisher.started.wait()
        await ActiveSignalService(self.tx,lambda:self.clock_now).cancel_signal(signal.id,self.actor.id)
        publisher.release.set();await sending
        async with self.sessions() as s:
            publication=await s.get(LimitPendingPublicationRecord,publication_id)
            self.assertIs(publication.status,PublicationStatus.DELIVERY_UNKNOWN)
            self.assertIsNone(publication.telegram_message_id)
            self.assertEqual(await s.scalar(select(func.count()).select_from(
                LimitCancellationPublicationRecord)),0)

    async def test_fast_stop_loss_while_pending_is_sending_preserves_chain_and_release(self):
        await self._fast_terminal_event_preserves_chain(Decimal("84"), MonitorStage.COMPLETED_SL)

    async def test_fast_final_tp_while_pending_is_sending_preserves_chain_and_release(self):
        await self._fast_terminal_event_preserves_chain(Decimal("105"), MonitorStage.COMPLETED_TARGETS)

    async def _fast_terminal_event_preserves_chain(self, exit_price, terminal_stage):
        await self.only_good_destination()
        signal = await self.signal_service.create_limit(self.command())
        publisher = BlockingPublisher()
        templates = DestinationMessageTemplateService(self.tx)
        pending = LimitPendingPublicationService(self.tx, publisher, templates, lambda: self.clock_now)
        await pending.plan_pending()
        pending_id = (await pending.pending_ids())[0]
        sending = asyncio.create_task(pending.process(pending_id))
        try:
            await asyncio.wait_for(publisher.started.wait(), 5)
            limits = LimitOrderService(self.sessions, clock=lambda: self.clock_now)
            self.assertTrue(await limits.observe(signal.id, Decimal("90"), PriceSourceType.COMPOSITE,
                                                 snapshot("90").source_prices, NOW))
            monitoring = MonitoringService(self.sessions, None, clock=lambda: self.clock_now)
            work, = await monitoring.load_work(100)
            self.assertTrue(await monitoring.evaluate(work, MarketDecision(
                exit_price, snapshot(str(exit_price)).source_prices, PriceSourceType.COMPOSITE, NOW)))
            async with self.sessions() as s:
                self.assertIs((await s.get(SignalMonitorStateRecord, signal.id)).stage, terminal_stage)
                self.assertTrue((await s.get(SignalPositionStateRecord, signal.id)).is_closed)
                self.assertIsNone(await s.scalar(select(DestinationTradeLockRecord.signal_id)))
                candidate_id = await s.scalar(select(SignalUpdateCandidateRecord.id))
                note_id = await s.scalar(select(SignalistNotificationRecord.id).where(
                    SignalistNotificationRecord.candidate_id == candidate_id))
            notifications = SignalistNotificationService(self.sessions, Mock(send=AsyncMock(return_value=33)))
            await notifications.deliver(note_id)
            await monitoring.decide_candidate(candidate_id, self.actor.id, True)
            updates = UpdatePublicationService(self.tx, Image(), publisher, lambda: self.clock_now,
                                               message_templates=templates)
            await updates.sweep()
            self.assertEqual(await updates.pending_ids(), ())
            async with self.sessions() as s:
                self.assertIs(await s.scalar(select(UpdatePublicationRequestRecord.status)),
                              PublicationRequestStatus.WAITING_INITIAL)
        finally:
            publisher.release.set()
            await sending

        initial = InitialPublicationService(self.tx, Image(), publisher, lambda: self.clock_now,
                                             message_templates=templates)
        await pending.recover()
        await initial.recover()
        await updates.recover()
        await updates.sweep()
        self.assertEqual(await updates.pending_ids(), ())
        initial_id, = await initial.pending_ids()
        await initial.process(initial_id)
        self.assertEqual(publisher.photos[0][3], 701)
        await updates.sweep()
        update_id, = await updates.pending_ids()
        await updates.process(update_id)
        self.assertEqual(len(publisher.text), 1)
        self.assertEqual(publisher.update_photos, [])
        details = SignalDetailsPublicationService(self.tx, publisher, lambda: self.clock_now,
                                                  message_templates=templates)
        details_id, = await details.pending_ids()
        await details.process(details_id)
        await updates.process(update_id)
        self.assertEqual(publisher.text[1][2], 801)
        if terminal_stage is MonitorStage.COMPLETED_TARGETS:
            update, = publisher.update_photos
            self.assertEqual(update[3], 902)
        else:
            self.assertEqual(publisher.text[2][2], 902)
        async with self.sessions() as s:
            self.assertIsNone(await s.scalar(select(DestinationTradeLockRecord.signal_id)))
            self.assertIs((await s.get(LimitPendingPublicationRecord, pending_id)).status,
                          PublicationStatus.SENT)

    async def test_trigger_before_admission_and_interrupted_pending_send(self):
        signal = await self.signal_service.create_limit(self.command())
        limits = LimitOrderService(self.sessions, clock=lambda: self.clock_now)
        self.assertTrue(await limits.observe(signal.id, Decimal("90"), PriceSourceType.COMPOSITE,
                                             snapshot("90").source_prices, NOW))
        publisher = BlockingPublisher()
        templates = DestinationMessageTemplateService(self.tx)
        pending = LimitPendingPublicationService(self.tx, publisher, templates, lambda: self.clock_now)
        await pending.plan_pending()
        interrupted_id, successful_id = await pending.pending_ids()
        interrupted = asyncio.create_task(pending.process(interrupted_id))
        await asyncio.wait_for(publisher.started.wait(), 5)
        interrupted.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await interrupted
        publisher = Publisher()
        restarted = LimitPendingPublicationService(self.tx, publisher, templates, lambda: self.clock_now)
        await restarted.recover()
        self.assertEqual(await restarted.pending_ids(), (successful_id,))
        await restarted.process(successful_id)
        await restarted.plan_pending()
        self.assertEqual(await restarted.pending_ids(), ())
        async with self.sessions() as s:
            unknown = await s.get(LimitPendingPublicationRecord, interrupted_id)
            self.assertIs(unknown.status, PublicationStatus.DELIVERY_UNKNOWN)
            self.assertIsNone(unknown.telegram_message_id)
            initial, = (await s.scalars(select(DestinationPublicationRecord))).all()
            self.assertEqual((initial.destination_id, initial.reply_to_message_id), (self.blocked, 701))

    async def test_trigger_transaction_rolls_back_before_retry(self):
        signal = await self.signal_service.create_limit(self.command())
        limits = LimitOrderService(self.sessions, clock=lambda: self.clock_now)
        with patch("chainura.persistence.publication_repository.SqlAlchemyPublicationRepository.queue_limit_initials",
                   side_effect=RuntimeError("fault before commit")):
            with self.assertRaisesRegex(RuntimeError, "fault before commit"):
                await limits.observe(signal.id, Decimal("90"), PriceSourceType.COMPOSITE,
                                     snapshot("90").source_prices, NOW)
        async with self.sessions() as s:
            self.assertIs((await s.get(SignalRecord, signal.id)).status, SignalLifecycleStatus.PENDING_ENTRY)
            for model in (LimitEntryTriggerRecord, SignalMonitorStateRecord, SignalPositionStateRecord):
                self.assertIsNone(await s.get(model, signal.id))
            self.assertIsNone(await s.scalar(select(SignalistNotificationRecord.id)))
        restarted = LimitOrderService(self.sessions)
        self.assertTrue(await restarted.observe(signal.id, Decimal("90"), PriceSourceType.COMPOSITE,
                                                snapshot("90").source_prices, NOW))

    async def test_fast_exit_update_can_publish_to_ready_destination_while_other_pending_sends(self):
        signal = await self.signal_service.create_limit(self.command())
        publisher = Publisher()
        templates = DestinationMessageTemplateService(self.tx)
        pending = LimitPendingPublicationService(self.tx, publisher, templates, lambda: self.clock_now)
        await pending.plan_pending()
        ready_id, late_id = await pending.pending_ids()
        await pending.process(ready_id)
        delayed = BlockingPublisher()
        pending.publisher = delayed
        sending = asyncio.create_task(pending.process(late_id))
        try:
            await asyncio.wait_for(delayed.started.wait(), 5)
            clock = lambda: self.clock_now
            orders = LimitOrderService(self.sessions, clock=clock)
            await orders.observe(signal.id, Decimal("90"), PriceSourceType.COMPOSITE,
                                 snapshot("90").source_prices, NOW)
            initial = InitialPublicationService(self.tx, Image(), publisher, clock, message_templates=templates)
            details = SignalDetailsPublicationService(self.tx, publisher, clock, message_templates=templates)
            await initial.process((await initial.pending_ids())[0])
            await details.process((await details.pending_ids())[0])
            monitoring = MonitoringService(self.sessions, None, clock=clock)
            work, = await monitoring.load_work(100)
            await monitoring.evaluate(work, MarketDecision(
                Decimal("84"), snapshot("84").source_prices, PriceSourceType.COMPOSITE, NOW))
            async with self.sessions() as s:
                candidate = await s.scalar(select(SignalUpdateCandidateRecord.id))
                note = await s.scalar(select(SignalistNotificationRecord.id).where(
                    SignalistNotificationRecord.candidate_id == candidate))
            direct = SignalistNotificationService(self.sessions, Mock(send=AsyncMock(return_value=55)))
            await direct.deliver(note)
            await monitoring.decide_candidate(candidate, self.actor.id, True)
            updates = UpdatePublicationService(self.tx, Image(), publisher, clock, message_templates=templates)
            await updates.sweep()
            item_id, = await updates.pending_ids()
            await updates.process(item_id)
            self.assertEqual(publisher.text[-1][2], 902)
            async with self.sessions() as s:
                self.assertIs(await s.scalar(select(UpdatePublicationRequestRecord.status)),
                              PublicationRequestStatus.WAITING_INITIAL)
        finally:
            delayed.release.set()
            await sending

        await initial.process((await initial.pending_ids())[0])
        await details.process((await details.pending_ids())[0])
        await updates.sweep()
        await updates.process((await updates.pending_ids())[0])
        await updates.sweep()
        self.assertEqual(await updates.pending_ids(), ())
        async with self.sessions() as s:
            self.assertIs(await s.scalar(select(UpdatePublicationRequestRecord.status)),
                          PublicationRequestStatus.COMPLETED)
        self.assertEqual(publisher.text[-1][2], 904)
