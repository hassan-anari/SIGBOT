import asyncio
import unittest
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import Mock
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import func,select,text

from chainura.application.limit_orders import LimitOrderService
from chainura.application.limit_publication import (LimitCancellationPublicationService,
    LimitPendingPublicationService)
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.application.publication import CancellationPublicationService,InitialPublicationService
from chainura.application.publication import DeliveryAmbiguous,DeliveryRateLimited,DeliveryRejected
from chainura.application.signal_service import (CreateLimitSignalCommand,CreatePendingSignalCommand,
    ReferencePriceResult,SignalService)
from chainura.application.signalist_notification import SignalistNotificationService
from chainura.application.active_signals import ActiveSignalService
from chainura.application.pagination import PageRequest
from chainura.domain.destination import Exchange
from chainura.domain.monitoring import NotificationStatus
from chainura.domain.publication import PublicationStatus
from chainura.domain.signal import (PriceSourceType,SignalEntryType,SignalLifecycleStatus,
    SignalMode,TradeDirection)
from chainura.domain.signalist import Signalist
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import (DestinationPublicationRecord,DestinationTradeLockRecord,
    InitialPublicationDailySlotRecord,LimitCancellationPublicationRecord,LimitEntryTriggerRecord,LimitPendingPublicationRecord,
    SignalCancellationPublicationRecord,
    SignalDetailsPublicationRecord,SignalMonitorStateRecord,SignalPositionStateRecord,
    SignalRecord,SignalistNotificationRecord)
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
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
    def __init__(self):self.text=[];self.photos=[]
    async def send_standalone_text(self,chat_id,body):self.text.append((chat_id,body));return 700+len(self.text)
    async def send_initial(self,chat_id,path,caption,reply_to_message_id=None):
        self.photos.append((chat_id,path,caption,reply_to_message_id));return 800+len(self.photos)
    async def send_text_update(self,chat_id,body,reply_to_message_id):
        self.text.append((chat_id,body,reply_to_message_id));return 900+len(self.text)
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
    async def render_initial_signal(self,*args):
        class Output:file_path=Path("/tmp/limit-test.png")
        return Output()
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

    async def test_long_production_style_lifecycle_and_retry(self):
        await self.create_blocking_market();signal=await self.signal_service.create_limit(self.command())
        duplicate=await self.signal_service.create_limit(self.command())
        self.assertEqual(signal.id,duplicate.id);self.assertIs(signal.entry_type,SignalEntryType.LIMIT)
        self.assertIs(signal.status,SignalLifecycleStatus.PENDING_ENTRY);self.assertEqual(self.prices.calls,2)
        publisher=Publisher();templates=DestinationMessageTemplateService(self.tx)
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
        limit=LimitOrderService(self.sessions,clock=lambda:self.clock_now)
        self.assertFalse(await limit.observe(signal.id,Decimal("91"),PriceSourceType.COMPOSITE,snapshot("91").source_prices,NOW))
        observed=await asyncio.gather(*[limit.observe(signal.id,Decimal("90"),PriceSourceType.COMPOSITE,snapshot("90").source_prices,NOW) for _ in range(2)])
        self.assertEqual(observed.count(True),1)
        async with self.sessions() as s:
            self.assertEqual(await s.scalar(select(func.count()).select_from(LimitEntryTriggerRecord).where(LimitEntryTriggerRecord.signal_id==signal.id)),1)
            note=await s.scalar(select(SignalistNotificationRecord).where(SignalistNotificationRecord.limit_trigger_signal_id==signal.id))
            self.assertIs(note.status,NotificationStatus.PENDING)
        transport=NotificationTransport();notifications=SignalistNotificationService(self.sessions,transport,max_attempts=1,retry_base_seconds=1,clock=lambda:self.clock_now)
        await notifications.deliver(note.id)
        async with self.sessions() as s:
            note=await s.get(SignalistNotificationRecord,note.id);self.assertIs(note.status,NotificationStatus.RETRY_WAIT);self.assertEqual(note.last_error_category,"rate_limited");self.assertEqual(note.retry_at,NOW+timedelta(seconds=12))
        self.clock_now+=timedelta(seconds=12);await notifications.deliver(note.id)
        self.assertTrue(await limit.activate(signal.id,self.actor.id));self.assertTrue(await limit.activate(signal.id,self.actor.id))
        async with self.sessions() as s:
            db=await s.get(SignalRecord,signal.id);self.assertIs(db.status,SignalLifecycleStatus.ACTIVE);self.assertEqual(db.entry_price,Decimal("90"))
            self.assertIsNotNone(await s.get(SignalPositionStateRecord,signal.id));self.assertIsNotNone(await s.get(SignalMonitorStateRecord,signal.id))
            activations=(await s.scalars(select(DestinationPublicationRecord).where(DestinationPublicationRecord.signal_id==signal.id))).all()
            self.assertEqual(len(activations),1);self.assertEqual(activations[0].reply_to_message_id,701)
            self.assertEqual(await s.scalar(select(func.count()).select_from(InitialPublicationDailySlotRecord).where(InitialPublicationDailySlotRecord.limit_publication_id.is_not(None))),1)
        initial=InitialPublicationService(self.tx,Image(),publisher,lambda:self.clock_now,message_templates=templates)
        await initial.process(activations[0].id)
        self.assertEqual(publisher.photos[0][3],701)
        async with self.sessions() as s:
            activation=await s.get(DestinationPublicationRecord,activations[0].id);self.assertIs(activation.status,PublicationStatus.SENT)
            self.assertIsNotNone(await s.scalar(select(SignalDetailsPublicationRecord).where(SignalDetailsPublicationRecord.signal_id==signal.id)))
            self.assertEqual(await s.scalar(select(func.count()).select_from(DestinationTradeLockRecord).where(DestinationTradeLockRecord.signal_id==signal.id)),1)

    async def test_short_threshold_and_moved_price_does_not_revert(self):
        signal=await self.signal_service.create_limit(self.command(TradeDirection.SHORT,"short"))
        service=LimitOrderService(self.sessions,clock=lambda:self.clock_now)
        self.assertFalse(await service.observe(signal.id,Decimal("109"),PriceSourceType.COMPOSITE,snapshot("109").source_prices,NOW))
        self.assertTrue(await service.observe(signal.id,Decimal("110"),PriceSourceType.COMPOSITE,snapshot("110").source_prices,NOW))
        self.assertFalse(await service.observe(signal.id,Decimal("100"),PriceSourceType.COMPOSITE,snapshot("100").source_prices,NOW))
        async with self.sessions() as s:self.assertIs((await s.get(SignalRecord,signal.id)).status,SignalLifecycleStatus.ENTRY_TRIGGERED)

    async def test_triggered_active_signal_fallback_and_atomic_cancellation(self):
        signal=await self.signal_service.create_limit(self.command(token="cancel-limit"))
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
