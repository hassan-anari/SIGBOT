import unittest
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from chainura.application.market_gateway import (BatchedMarketSnapshotProvider,
 MarketPriceWorker,MarketReferencePriceProvider,SymbolCatalogProvider)
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType,SignalMode
from models import Instrument,PriceTick
class Adapter:
 def __init__(self,name,ticks,instruments=()): self.name=name; self.ticks=ticks; self.instruments=instruments; self.symbol_map={}
 async def fetch_prices(self): return self.ticks
 async def fetch_instruments(self): return list(self.instruments)
class Probe:
 def __init__(self):
  self.instruments={
   'ourbit':[Instrument('ourbit','BTCUSDT','BTCUSDT','BTC')],
   'toobit':[Instrument('toobit','BTCUSDT','BTCUSDT','BTC')],
   'lbank':[Instrument('lbank','btc_usdt','BTCUSDT','BTC'),Instrument('lbank','doge_usdt','DOGEUSDT','DOGE')]}
  self.common_symbols={'BTCUSDT'}; self.adapters=[]; self.latest={}; self.clock=lambda:datetime.now(timezone.utc)
 async def discover(self): return None
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
  p=Probe(); tick=PriceTick('lbank','doge_usdt','DOGEUSDT',Decimal('.2'),datetime.now(timezone.utc),'fake'); p.adapters=[Adapter('lbank',[tick],p.instruments['lbank'])]
  result=await MarketReferencePriceProvider(p).get_reference_price(mode=SignalMode.SINGLE_EXCHANGE,canonical_symbol='DOGEUSDT',selected_exchange=Exchange.LBANK,native_symbol='doge_usdt')
  self.assertEqual(result.reference_price,Decimal('.2')); self.assertEqual(result.native_symbols,((Exchange.LBANK,'doge_usdt'),))

class RetryProbe:
 def __init__(self):
  self.calls=0; self.instruments={}; self.common_symbols=set()
  self.valid={
   'ourbit':[Instrument('ourbit','BTCUSDT','BTCUSDT','BTC')],
   'toobit':[Instrument('toobit','BTCUSDT','BTCUSDT','BTC')],
   'lbank':[Instrument('lbank','btc_usdt','BTCUSDT','BTC')]}
  self.adapters=[Adapter(name,[],rows) for name,rows in self.valid.items()]
 async def discover(self):
  self.calls+=1
  if self.calls==1:
   self.instruments={'ourbit':self.valid['ourbit'],'toobit':self.valid['toobit']}
   self.common_symbols={'STALEUSDT'}
   raise RuntimeError('partial provider failure')
  self.instruments={name:list(rows) for name,rows in self.valid.items()}
  self.common_symbols={'BTCUSDT'}
 def _mapping_for(self,name): return {x.canonical_symbol:x.exchange_symbol for x in self.instruments.get(name,())}

class CatalogReadinessTests(unittest.IsolatedAsyncioTestCase):
 async def test_partial_shared_failure_retries_without_restart(self):
  from chainura.errors import SymbolCatalogError
  probe=RetryProbe(); catalog=SymbolCatalogProvider(probe)
  with self.assertRaises(SymbolCatalogError): await catalog.search_shared('btc')
  self.assertEqual(probe.calls,1); self.assertEqual(probe.common_symbols,set())
  result=await catalog.search_shared('btc')
  self.assertEqual(probe.calls,2); self.assertEqual(result.items[0].canonical_symbol,'BTCUSDT')
  self.assertEqual(set(probe.instruments),{'ourbit','toobit','lbank'})
 async def test_nonempty_partial_mapping_is_not_ready(self):
  from chainura.errors import SymbolCatalogError
  probe=RetryProbe(); probe.instruments={'ourbit':probe.valid['ourbit']}
  catalog=SymbolCatalogProvider(probe)
  with self.assertRaises(SymbolCatalogError): await catalog.search_shared('btc')
  self.assertEqual(probe.calls,1)
 async def test_exchange_search_revalidates_partial_shared_rows(self):
  from chainura.errors import SymbolCatalogError
  probe=RetryProbe(); catalog=SymbolCatalogProvider(probe)
  with self.assertRaises(SymbolCatalogError): await catalog.search_shared('btc')
  # Simulate an unprocessed partial row left by the failed shared discovery.
  probe.instruments['lbank']=[Instrument('lbank','unprocessed','BADUSDT','BAD')]
  result=await catalog.search_exchange(Exchange.LBANK,'btc')
  self.assertEqual(tuple(x.native_symbol for x in result.items),('btc_usdt',))
  self.assertNotIn('unprocessed',probe.adapters[2].symbol_map)

class ProductionLikeAdapter:
 def __init__(self,name,native,prices):
  self.name=name;self.native=native;self.prices=list(prices);self.symbol_map={};self.price_calls=0;self.catalog_calls=0
 async def fetch_instruments(self):
  self.catalog_calls+=1;raise TimeoutError('catalog unavailable')
 async def fetch_prices(self):
  self.price_calls+=1
  raw=self.prices[min(self.price_calls-1,len(self.prices)-1)]
  if isinstance(raw,BaseException):raise raw
  canonical=self.symbol_map.get(self.native)
  if canonical is None:raise ValueError('no usable last prices')
  return [PriceTick(self.name,self.native,canonical,Decimal(raw),datetime.now(timezone.utc),'raw')]

def work(signal_id,symbol='BTCUSDT'):
 return SimpleNamespace(signal_id=signal_id,mode=SignalMode.COMPOSITE,selected_exchange=None,
  canonical_symbol=symbol,price_source_type=PriceSourceType.COMPOSITE,
  exchange_symbols=tuple((exchange,{'ourbit':'BTCUSDT','toobit':'BTCUSDT','lbank':'btc_usdt'}[exchange.value]) for exchange in (Exchange.OURBIT,Exchange.TOOBIT,Exchange.LBANK)))

class RestartMarketTests(unittest.IsolatedAsyncioTestCase):
 def probe(self,sequences=None):
  p=Probe();p.instruments={};p.common_symbols=set();p.max_price_age=__import__('datetime').timedelta(seconds=15);p.max_spread_percent=Decimal('5')
  p._require_aware=lambda value:value;p._spread_percent=lambda a,b:(max(a,b)-min(a,b))/min(a,b)*100
  sequences=sequences or {}
  p.adapters=[ProductionLikeAdapter('ourbit','BTCUSDT',sequences.get('ourbit',['99'])),ProductionLikeAdapter('toobit','BTCUSDT',sequences.get('toobit',['100'])),ProductionLikeAdapter('lbank','btc_usdt',sequences.get('lbank',['101']))]
  return p
 async def test_snapshot_hydrates_production_like_adapters_before_parsing(self):
  p=self.probe();catalog=SymbolCatalogProvider(p)
  decisions=await BatchedMarketSnapshotProvider(p,catalog).snapshot((work(1),work(2)))
  self.assertEqual(set(decisions),{1,2});self.assertEqual(decisions[1].reference_price,Decimal('100'))
  self.assertTrue(all(adapter.price_calls==1 for adapter in p.adapters))
  self.assertTrue(all(adapter.catalog_calls==0 for adapter in p.adapters))
 async def test_persisted_composite_mappings_parse_without_catalog_or_search(self):
  p=self.probe();catalog=SymbolCatalogProvider(p);catalog._shared_ready=True
  worker=MarketPriceWorker(p,catalog,lambda limit: self._works((work(1),work(2))))
  catalog.market_service=worker
  await worker.run_cycle()
  decisions=await BatchedMarketSnapshotProvider(p,catalog).snapshot((work(1),work(2)))
  self.assertEqual(set(decisions),{1,2});self.assertEqual(decisions[1].reference_price,Decimal('100'))
  self.assertEqual(len(decisions[1].source_prices),3)
  self.assertTrue(all(adapter.price_calls==1 for adapter in p.adapters))
  self.assertTrue(all(adapter.catalog_calls==0 for adapter in p.adapters))
 async def test_price_failure_is_retried_and_recovers_without_discovery(self):
  p=self.probe({'toobit':[TimeoutError('down'),'100']});catalog=SymbolCatalogProvider(p);catalog._shared_ready=True
  worker=MarketPriceWorker(p,catalog,lambda limit:self._works((work(1),)))
  catalog.market_service=worker
  await worker.run_cycle();first=(await BatchedMarketSnapshotProvider(p,catalog).snapshot((work(1),)))[1]
  self.assertEqual(len(first.source_prices),2)
  await worker.run_cycle();decision=(await BatchedMarketSnapshotProvider(p,catalog).snapshot((work(1),)))[1]
  self.assertEqual(decision.reference_price,Decimal('100'));self.assertEqual(p.adapters[1].price_calls,2)
 async def test_always_on_worker_polls_all_exchanges_repeatedly_with_zero_signals(self):
  p=self.probe({'toobit':['100',TimeoutError('temporary'),'100']});catalog=SymbolCatalogProvider(p);catalog._shared_ready=True
  for adapter in p.adapters:adapter.symbol_map[adapter.native]='BTCUSDT'
  worker=MarketPriceWorker(p,catalog,lambda limit:self._works(()),poll_seconds=.01);catalog.market_service=worker
  await worker.start();await __import__('asyncio').sleep(.055);await worker.stop()
  self.assertTrue(worker.task.done());self.assertTrue(all(adapter.price_calls>=3 for adapter in p.adapters))
  self.assertIn(('BTCUSDT','toobit'),p.latest)
 async def _works(self,value):return value

class DailyCatalogRefreshTests(unittest.IsolatedAsyncioTestCase):
 async def test_ready_catalog_refreshes_once_per_day(self):
  class DailyProbe:
   def __init__(self):
    self.now=datetime(2026,10,7,tzinfo=timezone.utc);self.calls=0;self.instruments={};self.common_symbols=set();self.adapters=[]
   def clock(self): return self.now
   async def discover(self):
    self.calls+=1
    self.instruments={
     'ourbit':[Instrument('ourbit','BTCUSDT','BTCUSDT','BTC')],
     'toobit':[Instrument('toobit','BTCUSDT','BTCUSDT','BTC')],
     'lbank':[Instrument('lbank','btc_usdt','BTCUSDT','BTC')],
     'weex':[Instrument('weex','BTCUSDT','BTCUSDT','BTC')],
    }
    self.common_symbols={'BTCUSDT'}
  p=DailyProbe();catalog=SymbolCatalogProvider(p)
  await catalog._ensure_discovered()
  self.assertEqual(p.calls,1)
  await catalog.refresh_catalogs_if_due()
  self.assertEqual(p.calls,1)
  p.now=p.now+__import__('datetime').timedelta(hours=23)
  await catalog.refresh_catalogs_if_due()
  self.assertEqual(p.calls,1)
  p.now=p.now+__import__('datetime').timedelta(hours=2)
  await catalog.refresh_catalogs_if_due()
  self.assertEqual(p.calls,2)
