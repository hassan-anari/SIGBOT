import unittest
from datetime import datetime,timezone
from decimal import Decimal
from sqlalchemy import text
from chainura.application.active_signals import ActiveSignalService
from chainura.application.pagination import PageRequest
from chainura.application.signal_service import *
from chainura.domain.destination import Exchange
from chainura.domain.signal import *
from chainura.domain.signalist import Signalist
from chainura.errors import PendingSignalStateError
from chainura.persistence.database import create_session_factory
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from integration_tests.support import create_safe_engine,migrate,require_safe_test_database

NOW=datetime(2026,8,9,12,tzinfo=timezone.utc)
def snap():return ReferencePriceResult(Decimal('100'),PriceSourceType.COMPOSITE,((Exchange.OURBIT,Decimal('100')),(Exchange.TOOBIT,Decimal('100'))),True,'BTCUSDT',((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt')))
class Prices:
 async def get_reference_price(self,**kw):return snap()

class Phase74CancellationTests(unittest.IsolatedAsyncioTestCase):
 @classmethod
 def setUpClass(cls):cls.safe=require_safe_test_database();migrate('head',cls.safe)
 @classmethod
 def tearDownClass(cls):cls.safe.cleanup()
 async def asyncSetUp(self):
  self.engine=create_safe_engine(self.safe);self.sessions=create_session_factory(self.engine)
  async with self.engine.begin() as c:await c.execute(text('DELETE FROM signals'));await c.execute(text('DELETE FROM signalists'))
  async with self.sessions() as s:
   async with s.begin():self.actor=await SqlAlchemySignalistRepository(s).add(Signalist.create(70001,'S'))
  self.factory=SqlAlchemySignalistTransactionFactory(self.sessions);self.signals=SignalService(self.factory,Prices(),lambda:NOW);self.active=ActiveSignalService(self.factory,lambda:NOW)
 async def asyncTearDown(self):await self.engine.dispose()
 async def pending(self,token='p'):
  return await self.signals.create_pending(CreatePendingSignalCommand(self.actor.id,token,SignalMode.COMPOSITE,'BTCUSDT','BTC','USDT',None,None,TradeDirection.LONG,10,snap()))
 async def test_pending_cancel_is_durable_terminal_and_idempotent(self):
  signal=await self.pending();result=await self.active.cancel_signal(signal.id,self.actor.id)
  self.assertTrue(result.cancelled);loaded=await self.signals.get_by_creation_token('p',self.actor.id)
  self.assertIs(loaded.status,SignalLifecycleStatus.CANCELLED);self.assertEqual(loaded.cancelled_at,NOW);self.assertEqual(loaded.take_profits,());self.assertIsNone(loaded.stop_loss)
  self.assertTrue((await self.active.cancel_signal(signal.id,self.actor.id)).already_cancelled)
  with self.assertRaises(PendingSignalStateError):await self.signals.finalize_details(FinalizeSignalDetailsCommand(signal.id,self.actor.id,(Decimal('110'),),Decimal('90')))
 async def test_active_open_cancel_preserves_levels_and_completed_is_excluded(self):
  pending=await self.pending('a');active=await self.signals.finalize_details(FinalizeSignalDetailsCommand(pending.id,self.actor.id,(Decimal('110'),Decimal('120')),Decimal('90')))
  page=await self.active.list_open(self.actor.id,PageRequest());self.assertEqual([x.id for x in page.items],[active.id])
  self.assertTrue((await self.active.cancel_signal(active.id,self.actor.id)).cancelled);loaded=await self.signals.get_by_creation_token('a',self.actor.id)
  self.assertEqual(loaded.take_profits,(Decimal('110'),Decimal('120')));self.assertEqual(loaded.stop_loss,Decimal('90'))
 async def test_owner_filter_and_deterministic_pagination(self):
  for i in range(10):await self.pending(f'p{i}')
  first=await self.active.list_open(self.actor.id,PageRequest(1));second=await self.active.list_open(self.actor.id,PageRequest(2))
  self.assertEqual((len(first.items),len(second.items),first.total),(8,2,10));self.assertTrue(set(x.id for x in first.items).isdisjoint(x.id for x in second.items))
 async def test_sent_initial_queues_text_cancellation_with_initial_parent(self):
  signal=await self.pending('published')
  async with self.engine.begin() as c:
   did=(await c.execute(text("insert into destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit) values('d',-1001,'channel','private','active',8) returning id"))).scalar_one()
   await c.execute(text("insert into destination_signalists(destination_id,signalist_id) values(:d,:a)"),{'d':did,'a':self.actor.id})
   await c.execute(text("update initial_publication_requests set status='completed' where signal_id=:s"),{'s':signal.id})
   await c.execute(text("insert into destination_publications(signal_id,destination_id,event_kind,selected_exchange,status,telegram_message_id,attempt_count,created_at,updated_at,sent_at) values(:s,:d,'initial','ourbit','sent',100,1,:n,:n,:n)"),{'s':signal.id,'d':did,'n':NOW})
  result=await self.active.cancel_signal(signal.id,self.actor.id);self.assertEqual(result.publication_count,1)
  class Publisher:
   def __init__(self):self.calls=[]
   async def send_text_update(self,*args):self.calls.append(args);return 101
  from chainura.application.publication import CancellationPublicationService
  from chainura.application.message_templates import DestinationMessageTemplateService
  publisher=Publisher();service=CancellationPublicationService(self.factory,publisher,lambda:NOW,message_templates=DestinationMessageTemplateService(self.factory))
  ids=await service.pending_ids();self.assertEqual(len(ids),1);await service.process(ids[0]);self.assertEqual(publisher.calls[0][2],100)
  async with self.engine.connect() as c:self.assertEqual((await c.execute(text("select status,telegram_message_id from signal_cancellation_publications"))).one(),('sent',101))

class Phase74DomainTests(unittest.TestCase):
 def base(self,**kw):
  values=dict(id=1,signalist_id=1,creation_token='x',mode=SignalMode.COMPOSITE,canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,direction=TradeDirection.LONG,entry_price=Decimal('100'),leverage=10,take_profits=(),stop_loss=None,reference_price=Decimal('100'),price_source_type=PriceSourceType.COMPOSITE,status=SignalLifecycleStatus.CANCELLED,exchange_symbols=((Exchange.OURBIT,'BTC'),(Exchange.TOOBIT,'BTC'),(Exchange.LBANK,'btc')),created_at=NOW,updated_at=NOW,initial_source_prices=((Exchange.OURBIT,Decimal('100')),(Exchange.TOOBIT,Decimal('100'))),cancelled_at=NOW);values.update(kw);return Signal(**values)
 def test_cancelled_accepts_only_inherited_shapes(self):
  self.base();self.base(take_profits=(Decimal('110'),),stop_loss=Decimal('90'))
  with self.assertRaises(Exception):self.base(take_profits=(Decimal('110'),),stop_loss=None)
