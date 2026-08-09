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
  async with self.engine.begin() as c:await c.execute(text('DELETE FROM signals'));await c.execute(text('DELETE FROM signalists'))
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
 async def test_excess_drift_writes_neither_signal_nor_publication(self):
  service=SignalService(self.factory,SequencePrices(snap('100.7001')),lambda:self.now)
  with self.assertRaises(InitialEntryDriftExceededError):await service.create_pending(self.command(snap('100')))
  async with self.engine.connect() as c:
   self.assertEqual(await c.scalar(text('select count(*) from signals')),0)
   self.assertEqual(await c.scalar(text('select count(*) from initial_publication_requests')),0)
