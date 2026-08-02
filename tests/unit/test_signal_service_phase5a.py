import unittest
from datetime import datetime,timezone
from decimal import Decimal
from chainura.application.signal_service import CreateSignalCommand,ReferencePriceResult,SignalService
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType,SignalMode,TradeDirection
from chainura.errors import EntryValidationError
class Provider:
 def __init__(self,result): self.result=result; self.calls=0
 async def get_reference_price(self,**kw): self.calls+=1; return self.result
class ServiceResultTests(unittest.IsolatedAsyncioTestCase):
 def command(self,mode=SignalMode.COMPOSITE,exchange=None,native=None): return CreateSignalCommand(1,'t',mode,'BTCUSDT','BTC','USDT',exchange,native,TradeDirection.LONG,Decimal('100'),2,(Decimal('101'),),Decimal('99'))
 async def validate(self,result,command=None):
  provider=Provider(result); service=SignalService(None,provider,lambda:datetime.now(timezone.utc)); return await service.validate_entry(command or self.command())
 def composite(self,**changes):
  values=dict(reference_price=Decimal('100'),price_source_type=PriceSourceType.COMPOSITE,source_prices=((Exchange.OURBIT,Decimal('100')),(Exchange.TOOBIT,Decimal('100'))),healthy=True,canonical_symbol='BTCUSDT',native_symbols=((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt')),failure_reason=None); values.update(changes); return ReferencePriceResult(**values)
 async def test_two_and_three_source_composite(self):
  await self.validate(self.composite()); await self.validate(self.composite(source_prices=((Exchange.OURBIT,Decimal('99')),(Exchange.TOOBIT,Decimal('100')),(Exchange.LBANK,Decimal('101')))))
 async def test_malformed_composite_results(self):
  bad=[self.composite(source_prices=((Exchange.OURBIT,Decimal('100')),)),self.composite(price_source_type=PriceSourceType.OURBIT_ONLY),self.composite(source_prices=((Exchange.OURBIT,Decimal('100')),(Exchange.OURBIT,Decimal('100')))),self.composite(native_symbols=((Exchange.OURBIT,'BTCUSDT'),)),self.composite(source_prices={Exchange.OURBIT:Decimal('100')})]
  for item in bad:
   with self.assertRaises(EntryValidationError): await self.validate(item)
 async def test_single_exchange_consistency(self):
  command=self.command(SignalMode.SINGLE_EXCHANGE,Exchange.LBANK,'btc_usdt')
  good=ReferencePriceResult(Decimal('100'),PriceSourceType.LBANK_ONLY,((Exchange.LBANK,Decimal('100')),),True,'BTCUSDT',((Exchange.LBANK,'btc_usdt'),))
  await self.validate(good,command)
  with self.assertRaises(EntryValidationError): await self.validate(ReferencePriceResult(Decimal('100'),PriceSourceType.TOOBIT_ONLY,((Exchange.LBANK,Decimal('100')),),True,'BTCUSDT',((Exchange.LBANK,'btc_usdt'),)),command)

class FakeSignals:
 def __init__(self,existing=None): self.existing=existing; self.adds=0
 async def get_by_creation_token(self,token): return self.existing
 async def add(self,signal): self.adds+=1; self.existing=signal; return signal
class FakeSignalists:
 async def get_by_id_fresh(self,signalist_id):
  from types import SimpleNamespace
  from chainura.domain.signalist import SignalistStatus
  return SimpleNamespace(status=SignalistStatus.ACTIVE)
class FakeTransaction:
 def __init__(self,signals): self.signals=signals; self.signalists=FakeSignalists(); self.commits=0
 async def __aenter__(self): return self
 async def __aexit__(self,*args): return None
 async def commit(self): self.commits+=1
class CountingFactory:
 def __init__(self,existing=None): self.calls=0; self.signals=FakeSignals(existing); self.transactions=[]
 def __call__(self):
  self.calls+=1; tx=FakeTransaction(self.signals); self.transactions.append(tx); return tx

class CreateValidatedBoundaryTests(ServiceResultTests):
 async def test_unhealthy_result_opens_no_transaction_and_persists_nothing(self):
  from chainura.errors import ReferencePriceUnavailableError
  factory=CountingFactory(); service=SignalService(factory,Provider(self.composite()))
  with self.assertRaises(ReferencePriceUnavailableError):
   await service.create_validated(self.command(),self.composite(healthy=False))
  self.assertEqual(factory.calls,0); self.assertEqual(factory.signals.adds,0)
 async def test_healthy_result_still_persists(self):
  factory=CountingFactory(); service=SignalService(factory,Provider(self.composite()),lambda:datetime.now(timezone.utc))
  result=await service.create_validated(self.command(),self.composite())
  self.assertEqual(result.creation_token,'t'); self.assertEqual(factory.calls,2); self.assertEqual(factory.signals.adds,1)
 async def test_existing_token_still_skips_provider(self):
  sentinel=object(); factory=CountingFactory(sentinel); provider=Provider(self.composite()); service=SignalService(factory,provider)
  self.assertIs(await service.create(self.command()),sentinel); self.assertEqual(provider.calls,0); self.assertEqual(factory.calls,1)
