import json
import unittest
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from sqlalchemy import func,select
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker

from chainura.application.market_gateway import BatchedMarketSnapshotProvider,SymbolCatalogProvider
from chainura.application.signal_comments import SignalCommentService,SignalCommentPublicationService
from chainura.application.publication import DeliveryRejected,SignalDetailsPublicationService,CancellationPublicationService
from chainura.application.active_signals import ActiveSignalService
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.domain.destination import DestinationAccessLevel,DestinationStatus,DestinationType,Exchange
from chainura.domain.monitoring import MonitorStage,SignalEventKind
from chainura.domain.publication import PublicationStatus
from chainura.domain.signal import PriceSourceType,SignalLifecycleStatus,SignalMode,TradeDirection
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.base import Base
from chainura.persistence.models import *
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from market import MarketProbe
from models import PriceTick

NOW=datetime(2026,8,13,12,tzinfo=timezone.utc)

class SharedMarket:
 def __init__(self,probe,price=None):self.probe=probe;self.price=price;self.calls=0
 async def refresh_prices(self,exchanges):
  self.calls+=1
  if self.price is not None:
   self.probe.latest[("BTCUSDT","ourbit")]=PriceTick("ourbit","BTCUSDT","BTCUSDT",self.price,NOW,"test")
  return {}
class Publisher:
 def __init__(self,fail_chat=None):self.calls=[];self.next=200;self.fail_chat=fail_chat
 async def send_text_update(self,chat,text,parent):
  self.calls.append(("text",chat,text,parent))
  if chat==self.fail_chat:raise DeliveryRejected("forbidden")
  self.next+=1;return self.next
 async def send_update(self,chat,path,caption,parent):self.calls.append(("photo",chat,caption,parent));self.next+=1;return self.next
 async def send_details(self,chat,text,parent):self.calls.append(("details",chat,text,parent));self.next+=1;return self.next
class Images:
 class Output:file_path=Path("/tmp/comment-test.png")
 def __init__(self,fail=False):self.fail=fail;self.references=[]
 async def render_snapshot_signal(self,signal,snapshot,exchange,destination_id):
  self.references.append(snapshot.observed_reference_price)
  if self.fail:raise ValueError("broken template")
  return self.Output()
 def cleanup(self,path):pass

class SignalCommentTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.engine=create_async_engine("sqlite+aiosqlite:///:memory:")
  async with self.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
  self.sessions=async_sessionmaker(self.engine,expire_on_commit=False);self.tx=SqlAlchemySignalistTransactionFactory(self.sessions)
  async with self.sessions() as s,s.begin():
   actor=SignalistRecord(telegram_user_id=7001,display_name="S",status=SignalistStatus.ACTIVE,created_at=NOW,updated_at=NOW);s.add(actor);await s.flush();self.actor=actor.id
   signal=SignalRecord(signalist_id=actor.id,creation_token="comment-signal",mode=SignalMode.SINGLE_EXCHANGE,canonical_symbol="BTCUSDT",base_asset="BTC",quote_asset="USDT",selected_exchange=Exchange.OURBIT,direction=TradeDirection.LONG,entry_price=Decimal("100"),leverage=10,stop_loss=Decimal("90"),reference_price=Decimal("100"),price_source_type=PriceSourceType.OURBIT_ONLY,status=SignalLifecycleStatus.ACTIVE,created_at=NOW-timedelta(minutes=10),updated_at=NOW);s.add(signal);await s.flush();self.signal=signal.id
   s.add_all([SignalExchangeSymbolRecord(signal_id=signal.id,exchange=Exchange.OURBIT,native_symbol="BTCUSDT"),SignalInitialPriceRecord(signal_id=signal.id,exchange=Exchange.OURBIT,price=Decimal("100")),SignalTakeProfitRecord(signal_id=signal.id,position=1,price=Decimal("110")),SignalMonitorStateRecord(signal_id=signal.id,stage=MonitorStage.PRE_TP1,next_tp_index=1,highest_tp_index=0,version=1,created_at=NOW,updated_at=NOW)])
   self.destinations=[]
   for i in range(2):
    d=DestinationRecord(internal_name=f"D{i}",telegram_chat_id=-100-i,destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PUBLIC,status=DestinationStatus.ACTIVE,daily_limit=10,created_at=NOW,updated_at=NOW);s.add(d);await s.flush();self.destinations.append(d.id)
    s.add_all([DestinationSignalistRecord(destination_id=d.id,signalist_id=actor.id,created_at=NOW),DestinationExchangePriorityRecord(destination_id=d.id,exchange=Exchange.OURBIT,position=1),DestinationPublicationRecord(signal_id=signal.id,destination_id=d.id,event_kind="initial",selected_exchange=Exchange.OURBIT,status=PublicationStatus.SENT,telegram_message_id=100+i,attempt_count=1,sent_at=NOW-timedelta(minutes=9),created_at=NOW-timedelta(minutes=9),updated_at=NOW-timedelta(minutes=9))])
  self.probe=MarketProbe(clock=lambda:NOW);self.catalog=SymbolCatalogProvider(self.probe);self.shared=SharedMarket(self.probe);self.catalog.market_service=self.shared;self.market=BatchedMarketSnapshotProvider(self.probe,self.catalog)
 async def asyncTearDown(self):await self.engine.dispose()
 async def rows(self,model):
  async with self.sessions() as s:return list((await s.scalars(select(model).order_by(model.id))).all())
 async def create(self,text="فعلاً باز بماند",snapshot=False):return await SignalCommentService(self.tx,self.market,lambda:NOW).create(self.signal,self.actor,text,snapshot)

 async def test_text_request_multiple_destinations_and_partial_failure(self):
  result=await self.create();self.assertTrue(result.created);self.assertEqual(result.publication_count,2)
  publisher=Publisher(fail_chat=-100);service=SignalCommentPublicationService(self.tx,publisher,clock=lambda:NOW)
  for row in await self.rows(SignalCommentPublicationRecord):await service.process(row.id)
  rows=await self.rows(SignalCommentPublicationRecord);self.assertEqual([r.status for r in rows],[PublicationStatus.FAILED,PublicationStatus.SENT]);self.assertEqual(len(publisher.calls),2)

 async def test_real_probe_latest_persists_snapshot_without_search(self):
  self.probe.latest[("BTCUSDT","ourbit")]=PriceTick("ourbit","BTCUSDT","BTCUSDT",Decimal("118.2"),NOW,"test")
  result=await self.create(snapshot=True);self.assertTrue(result.created);self.assertEqual(self.shared.calls,0)
  request=(await self.rows(SignalCommentRequestRecord))[0];self.assertEqual(request.reference_price,Decimal("118.2"));self.assertEqual(json.loads(request.source_prices_json),{"ourbit":"118.2"})
  async with self.sessions() as s:self.assertEqual(await s.scalar(select(func.count()).select_from(SignalEventRecord)),0);state=await s.get(SignalMonitorStateRecord,self.signal);self.assertIs(state.stage,MonitorStage.PRE_TP1);self.assertEqual(state.highest_tp_index,0)

 async def test_bounded_shared_refresh_fallback_and_captured_price_is_replayed(self):
  self.shared.price=Decimal("119");result=await self.create(snapshot=True);self.assertTrue(result.created);self.assertEqual(self.shared.calls,1)
  self.probe.latest[("BTCUSDT","ourbit")]=PriceTick("ourbit","BTCUSDT","BTCUSDT",Decimal("999"),NOW,"later")
  images=Images();publisher=Publisher();row=(await self.rows(SignalCommentPublicationRecord))[0];await SignalCommentPublicationService(self.tx,publisher,images,lambda:NOW).process(row.id)
  self.assertEqual(images.references,[Decimal("119")]);self.assertEqual(publisher.calls[0][0],"photo")

 async def test_snapshot_unavailable_and_caption_limit_create_nothing(self):
  unavailable=await self.create(snapshot=True);self.assertEqual(unavailable.reason,"market_unavailable")
  too_long=await self.create("x"*1025,True);self.assertEqual(too_long.reason,"caption_too_long")
  self.assertEqual(await self.rows(SignalCommentRequestRecord),[])

 async def test_render_failure_and_restart_recovery_do_not_strand(self):
  self.probe.latest[("BTCUSDT","ourbit")]=PriceTick("ourbit","BTCUSDT","BTCUSDT",Decimal("101"),NOW,"test");await self.create(snapshot=True)
  row=(await self.rows(SignalCommentPublicationRecord))[0];service=SignalCommentPublicationService(self.tx,Publisher(),Images(True),lambda:NOW);await service.process(row.id)
  async with self.sessions() as s,s.begin():
   failed=await s.get(SignalCommentPublicationRecord,row.id);self.assertIs(failed.status,PublicationStatus.FAILED)
   failed.status=PublicationStatus.RENDERING
   other=(await s.scalars(select(SignalCommentPublicationRecord).where(SignalCommentPublicationRecord.id!=row.id))).first();other.status=PublicationStatus.SENDING
  await service.recover();rows=await self.rows(SignalCommentPublicationRecord);self.assertEqual([r.status for r in rows],[PublicationStatus.PENDING,PublicationStatus.DELIVERY_UNKNOWN])

 async def test_details_barrier_then_comment_and_multiple_comment_chain(self):
  # Limit this ordering assertion to one destination.
  async with self.sessions() as s,s.begin():
   await s.execute(__import__('sqlalchemy').delete(DestinationPublicationRecord).where(DestinationPublicationRecord.destination_id==self.destinations[1]))
   s.add(SignalDetailsPublicationRecord(signal_id=self.signal,destination_id=self.destinations[0],status=PublicationStatus.PENDING,reply_to_message_id=100,created_at=NOW-timedelta(minutes=8),updated_at=NOW))
  await self.create();publisher=Publisher();comments=SignalCommentPublicationService(self.tx,publisher,clock=lambda:NOW);comment=(await self.rows(SignalCommentPublicationRecord))[0]
  await comments.process(comment.id);self.assertFalse(publisher.calls)
  await SignalDetailsPublicationService(self.tx,publisher,lambda:NOW).process(1);self.assertEqual(publisher.calls[-1][3],100)
  await comments.process(comment.id);self.assertEqual(publisher.calls[-1][3],201)
  second=await self.create("کامنت دوم");second_row=(await self.rows(SignalCommentPublicationRecord))[-1];await comments.process(second_row.id);self.assertEqual(publisher.calls[-1][3],202)

 async def test_terminal_race_supersedes_before_send(self):
  await self.create()
  async with self.sessions() as s,s.begin():
   state=await s.get(SignalMonitorStateRecord,self.signal);state.stage=MonitorStage.COMPLETED_SL;state.completed_at=NOW
  publisher=Publisher();service=SignalCommentPublicationService(self.tx,publisher,clock=lambda:NOW)
  for row in await self.rows(SignalCommentPublicationRecord):await service.process(row.id)
  self.assertFalse(publisher.calls);self.assertTrue(all(r.status is PublicationStatus.SUPERSEDED for r in await self.rows(SignalCommentPublicationRecord)))

 async def test_sent_comment_is_cancellation_parent(self):
  async with self.sessions() as s,s.begin():await s.execute(__import__('sqlalchemy').delete(DestinationPublicationRecord).where(DestinationPublicationRecord.destination_id==self.destinations[1]))
  await self.create();publisher=Publisher();comments=SignalCommentPublicationService(self.tx,publisher,clock=lambda:NOW);row=(await self.rows(SignalCommentPublicationRecord))[0];await comments.process(row.id)
  result=await ActiveSignalService(self.tx,lambda:NOW).cancel_signal(self.signal,self.actor);self.assertTrue(result.cancelled)
  cancellation=CancellationPublicationService(self.tx,publisher,lambda:NOW,message_templates=DestinationMessageTemplateService(self.tx));cid=(await cancellation.pending_ids())[0];await cancellation.process(cid)
  self.assertEqual(publisher.calls[-1][3],201)

if __name__=="__main__":unittest.main()
