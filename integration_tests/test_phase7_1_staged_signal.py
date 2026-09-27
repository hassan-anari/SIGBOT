import unittest
from datetime import datetime,timezone
from decimal import Decimal
from sqlalchemy import text
from chainura.application.signal_service import *
from chainura.domain.destination import Exchange
from chainura.domain.signal import *
from chainura.domain.signalist import Signalist
from chainura.errors import InitialEntryDriftExceededError,PendingSignalStateError
from chainura.persistence.database import create_session_factory
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from integration_tests.support import create_safe_engine,migrate,require_safe_test_database

def snap(price):
 return ReferencePriceResult(Decimal(price),PriceSourceType.COMPOSITE,((Exchange.OURBIT,Decimal(price)),(Exchange.TOOBIT,Decimal(price))),True,'BTCUSDT',((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt')))
class SequencePrices:
 def __init__(self,*values):self.values=list(values);self.calls=0
 async def get_reference_price(self,**kw):self.calls+=1;return self.values.pop(0)
class Stage71PersistenceTests(unittest.IsolatedAsyncioTestCase):
 @classmethod
 def setUpClass(cls):cls.safe=require_safe_test_database();migrate('head',cls.safe)
 @classmethod
 def tearDownClass(cls):cls.safe.cleanup()
 async def asyncSetUp(self):
  self.engine=create_safe_engine(self.safe);self.sessions=create_session_factory(self.engine)
  async with self.engine.begin() as c:
   await c.execute(text('DELETE FROM signals'));await c.execute(text('DELETE FROM destinations'));await c.execute(text('DELETE FROM signalists'))
  async with self.sessions() as s:
   async with s.begin():self.actor=await SqlAlchemySignalistRepository(s).add(Signalist.create(78901,'S'))
  self.factory=SqlAlchemySignalistTransactionFactory(self.sessions);self.now=datetime(2026,8,9,tzinfo=timezone.utc)
 async def asyncTearDown(self):await self.engine.dispose()
 def command(self,p0):return CreatePendingSignalCommand(self.actor.id,'stage-token',SignalMode.COMPOSITE,'BTCUSDT','BTC','USDT',None,None,TradeDirection.LONG,10,p0)
 async def test_pending_survives_restart_then_activates_once_preserving_p0_p1(self):
  provider=SequencePrices(snap('100.50'))
  service=SignalService(self.factory,provider,lambda:self.now)
  pending=await service.create_pending(self.command(snap('100')))
  self.assertIs(pending.status,SignalLifecycleStatus.PENDING_DETAILS);self.assertEqual(pending.take_profits,());self.assertIsNone(pending.stop_loss)
  self.assertEqual(pending.entry_price,Decimal('100'));self.assertEqual(pending.initial_source_prices[0][1],Decimal('100.50'))
  # New service instance simulates loss of all process/Redis conversational state.
  restarted=SignalService(self.factory,SequencePrices(),lambda:self.now)
  resumed=await restarted.get_pending_for_owner(pending.id,self.actor.id);self.assertEqual(resumed.id,pending.id)
  active=await restarted.finalize_details(FinalizeSignalDetailsCommand(pending.id,self.actor.id,(Decimal('101'),Decimal('102')),Decimal('99')))
  self.assertEqual(active.entry_price,Decimal('100'));self.assertEqual(active.initial_source_prices,pending.initial_source_prices)
  with self.assertRaises(PendingSignalStateError):await restarted.finalize_details(FinalizeSignalDetailsCommand(pending.id,self.actor.id,(Decimal('101'),),Decimal('99')))
  async with self.engine.connect() as c:
   self.assertEqual(await c.scalar(text("select count(*) from signal_monitor_states where signal_id=:s"),{'s':pending.id}),1)
   self.assertEqual(await c.scalar(text("select count(*) from signal_position_states where signal_id=:s"),{'s':pending.id}),1)
   self.assertEqual((await c.execute(text("select remaining_fraction,realized_weighted_roi,effective_stop_price,version from signal_position_states where signal_id=:s"),{'s':pending.id})).one(),('1','0','99',1))
 async def test_excess_drift_writes_neither_signal_nor_publication(self):
  service=SignalService(self.factory,SequencePrices(snap('100.7001')),lambda:self.now)
  with self.assertRaises(InitialEntryDriftExceededError):await service.create_pending(self.command(snap('100')))
  async with self.engine.connect() as c:
   self.assertEqual(await c.scalar(text('select count(*) from signals')),0)
   self.assertEqual(await c.scalar(text('select count(*) from initial_publication_requests')),0)

class PublicationRepairTests(Stage71PersistenceTests):
 async def _pending(self,token='publication-token'):
  service=SignalService(self.factory,SequencePrices(snap('100.1')),lambda:self.now)
  command=CreatePendingSignalCommand(self.actor.id,token,SignalMode.COMPOSITE,'BTCUSDT','BTC','USDT',None,None,TradeDirection.LONG,10,snap('100'))
  return await service.create_pending(command)
 async def _destination_and_initial(self,signal,message_id=777):
  async with self.engine.begin() as c:
   did=(await c.execute(text("insert into destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit) values(:n,-1001,'channel','private','active',5) returning id"),{'n':'d-'+signal.creation_token})).scalar_one()
   await c.execute(text("insert into destination_publications(signal_id,destination_id,event_kind,selected_exchange,status,telegram_message_id,attempt_count,created_at,updated_at,sent_at) values(:s,:d,'initial','ourbit','sent',:m,1,:n,:n,:n)"),{'s':signal.id,'d':did,'m':message_id,'n':self.now})
  return did
 async def test_notification_normal_recovery_and_sent_restart(self):
  from chainura.application.publication import InitialPublicationService
  pending=await self._pending()
  async with self.engine.begin() as c:
   await c.execute(text("insert into initial_publication_notifications(signal_id,status,outcome,created_at,updated_at) values(:s,'pending','success',:n,:n)"),{'s':pending.id,'n':self.now})
  class Publisher:
   def __init__(self):self.calls=[]
   async def send_initial_status(self,user,text,markup):self.calls.append((user,text,markup));return 909
  publisher=Publisher();service=InitialPublicationService(self.factory,None,publisher,lambda:self.now)
  self.assertTrue(await service.notify_completed());self.assertFalse(await service.notify_completed())
  async with self.engine.connect() as c:self.assertEqual((await c.execute(text("select status,telegram_message_id from initial_publication_notifications where signal_id=:s"),{'s':pending.id})).one(),('sent',909))
  await service.recover();self.assertFalse(await service.notify_completed());self.assertEqual(len(publisher.calls),1)
  async with self.engine.begin() as c:await c.execute(text("update initial_publication_notifications set status='sending',telegram_message_id=NULL,sent_at=NULL where signal_id=:s"),{'s':pending.id})
  await service.recover()
  async with self.engine.connect() as c:self.assertEqual(await c.scalar(text("select status from initial_publication_notifications where signal_id=:s"),{'s':pending.id}),'pending')
  self.assertTrue(await service.notify_completed());self.assertEqual(len(publisher.calls),2)
 async def _details(self,outcomes):
  from chainura.application.publication import SignalDetailsPublicationService
  pending=await self._pending('details-token');await self._destination_and_initial(pending)
  await SignalService(self.factory,SequencePrices(),lambda:self.now).finalize_details(FinalizeSignalDetailsCommand(pending.id,self.actor.id,(Decimal('101'),),Decimal('99')))
  async with self.engine.connect() as c:item=await c.scalar(text("select id from signal_details_publications where signal_id=:s"),{'s':pending.id})
  class Publisher:
   def __init__(self,values):self.values=list(values);self.calls=0;self.arguments=[]
   async def send_details(self,*args):
    self.calls+=1;self.arguments.append(args);value=self.values.pop(0)
    if isinstance(value,BaseException):raise value
    return value
  publisher=Publisher(outcomes);service=SignalDetailsPublicationService(self.factory,publisher,lambda:self.now,max_attempts=3,retry_base_seconds=1)
  return item,service,publisher
 async def test_details_three_attempt_limit_and_successful_retry(self):
  from chainura.application.publication import DeliveryRateLimited
  item,service,publisher=await self._details([DeliveryRateLimited(0),DeliveryRateLimited(0),DeliveryRateLimited(0)])
  for expected_status,attempts in [('retry_wait',1),('retry_wait',2),('failed',3)]:
   await service.process(item)
   async with self.engine.connect() as c:self.assertEqual((await c.execute(text("select status,attempt_count from signal_details_publications where id=:i"),{'i':item})).one(),(expected_status,attempts))
  self.assertEqual(publisher.calls,3)
  # A separate publication succeeds on its second actual attempt.
  async with self.engine.begin() as c:
   await c.execute(text("delete from signals"));await c.execute(text("delete from destinations"))
  item,service,publisher=await self._details([DeliveryRateLimited(0),456])
  await service.process(item);await service.process(item)
  async with self.engine.connect() as c:self.assertEqual((await c.execute(text("select status,attempt_count,telegram_message_id from signal_details_publications where id=:i"),{'i':item})).one(),('sent',2,456))
  self.assertEqual(publisher.arguments[-1][-1],777)
 async def test_details_preparation_failure_becomes_failed_not_stuck_sending(self):
  from unittest.mock import patch
  item,service,publisher=await self._details([123])
  with patch('chainura.application.publication.build_signal_details_text',side_effect=ValueError('bad persisted details')):
   await service.process(item)
  async with self.engine.connect() as c:
   self.assertEqual((await c.execute(text("select status,attempt_count from signal_details_publications where id=:i"),{'i':item})).one(),('failed',1))
  self.assertEqual(publisher.calls,0)
