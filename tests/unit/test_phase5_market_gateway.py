import unittest
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from chainura.application.market_gateway import MarketReferencePriceProvider,SymbolCatalogProvider
from chainura.domain.destination import Exchange
from chainura.domain.signal import SignalMode
from models import Instrument,PriceTick
class Adapter:
 def __init__(self,name,ticks): self.name=name; self.ticks=ticks
 async def fetch_prices(self): return self.ticks
class Probe:
 def __init__(self):
  self.instruments={
   'ourbit':[Instrument('ourbit','BTCUSDT','BTCUSDT','BTC')],
   'toobit':[Instrument('toobit','BTCUSDT','BTCUSDT','BTC')],
   'lbank':[Instrument('lbank','btc_usdt','BTCUSDT','BTC'),Instrument('lbank','doge_usdt','DOGEUSDT','DOGE')]}
  self.common_symbols={'BTCUSDT'}; self.adapters=[]; self.latest={}; self.clock=lambda:datetime.now(timezone.utc)
 def _mapping_for(self,e): return {x.canonical_symbol:x.exchange_symbol for x in self.instruments[e]}
 async def fetch_once(self): pass
 def _analyze_symbol(self,s,now): return {'reference_price':Decimal('100'),'status':'healthy','prices':{'ourbit':Decimal('99'),'toobit':Decimal('100'),'lbank':Decimal('101')},'reason':None}
class GatewayTests(unittest.IsolatedAsyncioTestCase):
 async def test_shared_and_native_search_are_verified_and_paged(self):
  p=Probe(); c=SymbolCatalogProvider(p)
  shared=await c.search_shared('btc',1); native=await c.search_exchange(Exchange.LBANK,'doge',1)
  self.assertEqual(shared.items[0].canonical_symbol,'BTCUSDT'); self.assertEqual(native.items[0].native_symbol,'doge_usdt')
  self.assertIsNone(await c.get_exchange_contract(Exchange.LBANK,'arbitrary'))
 async def test_composite_reuses_probe_analysis(self):
  result=await MarketReferencePriceProvider(Probe()).get_reference_price(mode=SignalMode.COMPOSITE,canonical_symbol='BTCUSDT',selected_exchange=None,native_symbol=None)
  self.assertEqual(result.reference_price,Decimal('100')); self.assertEqual(len(result.source_prices),3); self.assertEqual(len(result.native_symbols),3)
 async def test_single_reuses_selected_exchange_adapter(self):
  p=Probe(); tick=PriceTick('lbank','doge_usdt','DOGEUSDT',Decimal('.2'),datetime.now(timezone.utc),'fake'); p.adapters=[Adapter('lbank',[tick])]
  result=await MarketReferencePriceProvider(p).get_reference_price(mode=SignalMode.SINGLE_EXCHANGE,canonical_symbol='DOGEUSDT',selected_exchange=Exchange.LBANK,native_symbol='doge_usdt')
  self.assertEqual(result.reference_price,Decimal('.2')); self.assertEqual(result.native_symbols,((Exchange.LBANK,'doge_usdt'),))
