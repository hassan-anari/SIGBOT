import unittest
from datetime import datetime,timezone
from decimal import Decimal
from sqlalchemy import text
from chainura.application.signal_service import CreateSignalCommand,ReferencePriceResult,SignalService
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType,SignalMode,TradeDirection
from chainura.domain.signalist import Signalist
from chainura.persistence.database import create_session_factory
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from integration_tests.support import create_safe_engine,migrate,require_safe_test_database
class Prices:
 async def get_reference_price(self,**kw): return ReferencePriceResult(Decimal('100.00'),PriceSourceType.COMPOSITE,{Exchange.OURBIT:Decimal('100'),Exchange.TOOBIT:Decimal('100')},True,'BTCUSDT',{Exchange.OURBIT:'BTCUSDT',Exchange.TOOBIT:'BTCUSDT'})
class SignalPersistenceTests(unittest.IsolatedAsyncioTestCase):
 @classmethod
 def setUpClass(cls): cls.safe=require_safe_test_database(); migrate('head',cls.safe)
 @classmethod
 def tearDownClass(cls): cls.safe.cleanup()
 async def asyncSetUp(self):
  self.engine=create_safe_engine(self.safe); self.sessions=create_session_factory(self.engine)
  async with self.engine.begin() as c:
   await c.execute(text('DELETE FROM signals')); await c.execute(text('DELETE FROM signalists'))
  async with self.sessions() as s:
   await s.begin(); self.actor=await SqlAlchemySignalistRepository(s).add(Signalist.create(98123,'S')); await s.commit()
  self.service=SignalService(SqlAlchemySignalistTransactionFactory(self.sessions),Prices(),lambda:datetime(2026,8,1,tzinfo=timezone.utc))
 async def asyncTearDown(self): await self.engine.dispose()
 def command(self): return CreateSignalCommand(self.actor.id,'token',SignalMode.COMPOSITE,'BTCUSDT','BTC','USDT',None,None,TradeDirection.LONG,Decimal('100.000'),7,(Decimal('101.010'),Decimal('102.020')),Decimal('99.990'))
 async def test_exact_round_trip_order_and_idempotency(self):
  one=await self.service.create(self.command()); two=await self.service.create(self.command())
  self.assertEqual(one.id,two.id); self.assertEqual(one.entry_price,Decimal('100.000')); self.assertEqual(one.take_profits,(Decimal('101.010'),Decimal('102.020')))
  async with self.engine.connect() as c: self.assertEqual(await c.scalar(text('select count(*) from signals')),1)
