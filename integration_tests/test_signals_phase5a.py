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
 async def get_reference_price(self,**kw): return ReferencePriceResult(Decimal('100.00'),PriceSourceType.COMPOSITE,((Exchange.OURBIT,Decimal('100')),(Exchange.TOOBIT,Decimal('100'))),True,'BTCUSDT',((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt')))
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
  self.assertEqual(one.initial_source_prices,((Exchange.OURBIT,Decimal('100')),(Exchange.TOOBIT,Decimal('100'))))
  async with self.engine.connect() as c:
   self.assertEqual(await c.scalar(text('select count(*) from signals')),1)
   self.assertEqual(await c.scalar(text('select count(*) from signal_initial_prices')),2)
 async def test_snapshot_mapping_composite_fk_and_uniqueness(self):
  from sqlalchemy.exc import IntegrityError
  one=await self.service.create(self.command())
  async with self.engine.begin() as c:
   await c.execute(text("delete from signal_exchange_symbols where signal_id=:id and exchange='lbank'"),{'id':one.id})
   with self.assertRaises(IntegrityError):
    await c.execute(text("insert into signal_initial_prices(signal_id,exchange,price) values (:id,'lbank','100')"),{'id':one.id})

class GatePrices(Prices):
 def __init__(self):
  import asyncio
  self.arrived=0; self.both=asyncio.Event(); self.release=asyncio.Event(); self.fail=False
 async def get_reference_price(self,**kw):
  if self.fail: raise RuntimeError('provider secret')
  self.arrived+=1
  if self.arrived==2: self.both.set()
  await self.release.wait()
  return await super().get_reference_price(**kw)

class ConcurrentSignalPersistenceTests(SignalPersistenceTests):
 async def test_concurrent_same_token_is_one_aggregate(self):
  import asyncio
  gate=GatePrices(); service=SignalService(SqlAlchemySignalistTransactionFactory(self.sessions),gate,lambda:datetime(2026,8,1,tzinfo=timezone.utc))
  first=asyncio.create_task(service.create(self.command())); second=asyncio.create_task(service.create(self.command()))
  await gate.both.wait(); gate.release.set(); one,two=await asyncio.gather(first,second)
  self.assertEqual(one.id,two.id)
  async with self.engine.connect() as c:
   self.assertEqual(await c.scalar(text('select count(*) from signals')),1)
   self.assertEqual(await c.scalar(text('select count(*) from signal_take_profits')),2)
   self.assertEqual(await c.scalar(text('select count(*) from signal_exchange_symbols')),3)
 async def test_existing_token_skips_failed_provider(self):
  gate=GatePrices(); gate.release.set(); service=SignalService(SqlAlchemySignalistTransactionFactory(self.sessions),gate,lambda:datetime(2026,8,1,tzinfo=timezone.utc))
  one=await service.create(self.command()); gate.fail=True; two=await service.create(self.command())
  self.assertEqual(one.id,two.id)
