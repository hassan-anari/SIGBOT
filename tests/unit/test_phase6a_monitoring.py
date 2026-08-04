import unittest
from datetime import datetime,timezone
from decimal import Decimal
from chainura.domain.destination import Exchange
from chainura.domain.signal import TradeDirection,PriceSourceType
from chainura.domain.monitoring import (MarketDecision,MonitorStage,SignalEventKind,
 evaluate_price,leveraged_roi,entry_distance_percent,favorable_percent)
from chainura.application.signalist_notification import event_text,prompt_text

NOW=datetime(2026,8,4,tzinfo=timezone.utc)
def market(price):return MarketDecision(Decimal(price),((Exchange.OURBIT,Decimal(price)),(Exchange.TOOBIT,Decimal(price))),PriceSourceType.COMPOSITE,NOW)

class MonitoringRuleTests(unittest.TestCase):
 def evaluate(self,direction,price,next_index=1,stage=MonitorStage.PRE_TP1):
  return evaluate_price(direction=direction,entry=Decimal('100'),leverage=10,take_profits=(Decimal('110'),Decimal('120'),Decimal('130')) if direction is TradeDirection.LONG else (Decimal('90'),Decimal('80'),Decimal('70')),stop_loss=Decimal('95') if direction is TradeDirection.LONG else Decimal('105'),next_tp_index=next_index,stage=stage,market=market(price))
 def test_long_and_short_tp_and_exact_roi(self):
  long=self.evaluate(TradeDirection.LONG,'111')[0];short=self.evaluate(TradeDirection.SHORT,'89')[0]
  self.assertEqual((long.kind,long.tp_index,long.leveraged_roi),(SignalEventKind.TP_HIT,1,Decimal('100')))
  self.assertEqual((short.kind,short.tp_index,short.leveraged_roi),(SignalEventKind.TP_HIT,1,Decimal('100')))
 def test_sl_before_tp1_and_ignored_after(self):
  self.assertEqual(self.evaluate(TradeDirection.LONG,'94')[0].kind,SignalEventKind.SL_HIT)
  self.assertEqual(self.evaluate(TradeDirection.SHORT,'106')[0].kind,SignalEventKind.SL_HIT)
  self.assertEqual(self.evaluate(TradeDirection.LONG,'94',2,MonitorStage.POST_TP1),())
 def test_multi_cross_is_ordered_and_uses_threshold_roi(self):
  events=self.evaluate(TradeDirection.LONG,'135')
  self.assertEqual([x.tp_index for x in events],[1,2,3]);self.assertEqual(events[-1].threshold_price,Decimal('130'));self.assertEqual(events[-1].observed_reference_price,Decimal('135'));self.assertEqual(events[-1].leveraged_roi,Decimal('300'))
 def test_completed_state_emits_nothing(self):
  self.assertEqual(self.evaluate(TradeDirection.LONG,'135',1,MonitorStage.COMPLETED_SL),())
 def test_high_precision_never_uses_float(self):
  value=leveraged_roi(TradeDirection.LONG,Decimal('3'),Decimal('4'),7)
  self.assertIsInstance(value,Decimal);self.assertEqual(value,Decimal(7)/Decimal(3)*Decimal(100))
 def test_breakeven_math(self):
  self.assertEqual(favorable_percent(TradeDirection.LONG,Decimal('100'),Decimal('101')),Decimal('1'))
  self.assertEqual(favorable_percent(TradeDirection.SHORT,Decimal('100'),Decimal('99')),Decimal('1'))
  self.assertEqual(entry_distance_percent(Decimal('100'),Decimal('100.1')),Decimal('.1'))

class PresentationTests(unittest.TestCase):
 def test_event_copy_is_persian_escaped_and_bidi_isolated(self):
  text=event_text(SignalEventKind.TP_HIT,'X&Y','12.3','10',2)
  self.assertIn('تارگت',text);self.assertIn('X&amp;Y',text);self.assertIn('\u2068',text);self.assertNotIn('#پیامک',text)
 def test_prompt_copy(self):
  text=prompt_text('BTCUSDT',Decimal('100'),Decimal('100.05'))
  self.assertIn('بریک‌ایون',text);self.assertIn('قیمت ورود',text)

import asyncio
from datetime import timedelta
from models import PriceTick, Instrument
from chainura.application.market_gateway import BatchedMarketSnapshotProvider, SymbolCatalogProvider
from chainura.application.monitoring import MonitorWork
from chainura.domain.signal import SignalMode
from chainura.telegram.event_routes import breakeven_prompt_keyboard, BreakevenPromptCallback

class FakeAdapter:
 def __init__(self,name,ticks=(),fail=False,instrument_fail=False,instruments=()):
  self.name=name; self.ticks=list(ticks); self.calls=0; self.instrument_calls=0; self.symbol_map={}; self.fail=fail; self.instrument_fail=instrument_fail; self.instruments=list(instruments)
 async def fetch_instruments(self):
  self.instrument_calls+=1
  if self.instrument_fail: raise RuntimeError('catalog down')
  return self.instruments
 async def fetch_prices(self):
  self.calls+=1
  if self.fail: raise RuntimeError('down')
  return self.ticks

class FakeProbe:
 def __init__(self,now):
  self.clock=lambda:now; self.max_price_age=timedelta(seconds=15); self.latest={}; self.common_symbols=set(); self.instruments={'ourbit':[],'toobit':[],'lbank':[]}
  self.adapters=[FakeAdapter('ourbit'),FakeAdapter('toobit'),FakeAdapter('lbank')]
 def _require_aware(self,value): return value
 def _analyze_symbol(self,symbol,now):
  prices={e:self.latest[(symbol,e)].last_price for e in ('ourbit','toobit','lbank') if (symbol,e) in self.latest and now-self.latest[(symbol,e)].received_at<=self.max_price_age}
  if len(prices)<2:return {'status':'insufficient','prices':prices,'reference_price':None}
  vals=sorted(prices.values())
  return {'status':'healthy' if len(vals)==3 else 'degraded','prices':prices,'reference_price':vals[1] if len(vals)==3 else sum(vals)/Decimal('2')}

class Phase6ACorrectionTests(unittest.TestCase):
 def test_breakeven_prompt_keyboard_contains_typed_compact_buttons(self):
  markup=breakeven_prompt_keyboard(42,3)
  buttons=markup.inline_keyboard[0]
  self.assertEqual([b.text for b in buttons],['✅ ثبت بریک‌ایون','❌ فعلاً نه'])
  packed=buttons[0].callback_data
  parsed=BreakevenPromptCallback.unpack(packed)
  self.assertEqual((parsed.action,parsed.prompt_id,parsed.cycle),('a',42,3))
  self.assertLess(len(packed),64)
 def test_single_exchange_snapshot_uses_native_symbol_outside_common_list(self):
  async def run():
   now=NOW; probe=FakeProbe(now); probe.common_symbols={'BTCUSDT'}
   tick=PriceTick('ourbit','NATIVE_X','OUTSIDEUSDT',Decimal('111'),now,'fake')
   probe.adapters[0].ticks=[tick]; probe.adapters[1].fail=True; probe.adapters[2].fail=True
   catalog=SymbolCatalogProvider(probe); catalog._shared_ready=True; catalog._exchange_ready={Exchange.OURBIT}
   provider=BatchedMarketSnapshotProvider(probe,catalog)
   work=MonitorWork(7,1,SignalMode.SINGLE_EXCHANGE,'OUTSIDEUSDT',Exchange.OURBIT,TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('95'),PriceSourceType.OURBIT_ONLY,((Exchange.OURBIT,'NATIVE_X'),),now)
   snapshot=await provider.snapshot((work,))
   self.assertIn(7,snapshot)
   self.assertEqual(snapshot[7].reference_price,Decimal('111'))
   self.assertEqual(snapshot[7].source_prices,((Exchange.OURBIT,Decimal('111')),))
   self.assertEqual([a.calls for a in probe.adapters],[1,0,0])
  asyncio.run(run())
 def test_persisted_single_exchange_survives_selected_catalog_outage(self):
  async def run():
   now=NOW; tick=PriceTick('ourbit','NATIVE_X','OUTSIDEUSDT',Decimal('111'),now,'fake'); probe=FakeProbe(now)
   probe.adapters[0].ticks=[tick]; probe.adapters[0].instrument_fail=True
   catalog=SymbolCatalogProvider(probe); provider=BatchedMarketSnapshotProvider(probe,catalog)
   work=MonitorWork(8,1,SignalMode.SINGLE_EXCHANGE,'OUTSIDEUSDT',Exchange.OURBIT,TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('95'),PriceSourceType.OURBIT_ONLY,((Exchange.OURBIT,'NATIVE_X'),),now)
   snapshot=await provider.snapshot((work,))
   self.assertIn(8,snapshot)
   self.assertEqual(snapshot[8].reference_price,Decimal('111'))
   self.assertEqual([a.instrument_calls for a in probe.adapters],[0,0,0])
   self.assertEqual([a.calls for a in probe.adapters],[1,0,0])
  asyncio.run(run())
 def test_mixed_persisted_composite_and_single_batch_uses_each_price_adapter_once(self):
  async def run():
   now=NOW; probe=FakeProbe(now)
   probe.adapters[0].ticks=[PriceTick('ourbit','NATIVE_X','OUTSIDEUSDT',Decimal('111'),now,'fake'),PriceTick('ourbit','BTC-USDT','BTCUSDT',Decimal('110'),now,'fake')]
   probe.adapters[1].ticks=[PriceTick('toobit','BTCUSDT','BTCUSDT',Decimal('110'),now,'fake')]; probe.adapters[1].instrument_fail=True
   probe.adapters[2].ticks=[PriceTick('lbank','btc_usdt','BTCUSDT',Decimal('110'),now,'fake')]
   catalog=SymbolCatalogProvider(probe); provider=BatchedMarketSnapshotProvider(probe,catalog)
   composite=MonitorWork(9,1,SignalMode.COMPOSITE,'BTCUSDT',None,TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('95'),PriceSourceType.COMPOSITE,((Exchange.OURBIT,'BTC-USDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt')),now)
   single=MonitorWork(10,1,SignalMode.SINGLE_EXCHANGE,'OUTSIDEUSDT',Exchange.OURBIT,TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('95'),PriceSourceType.OURBIT_ONLY,((Exchange.OURBIT,'NATIVE_X'),),now)
   snapshot=await provider.snapshot((composite,single))
   self.assertIn(9,snapshot); self.assertIn(10,snapshot)
   self.assertEqual([a.calls for a in probe.adapters],[1,1,1])
   self.assertEqual([a.instrument_calls for a in probe.adapters],[0,0,0])
  asyncio.run(run())
 def test_selected_exchange_failure_skips_single_cleanly(self):
  async def run():
   now=NOW; probe=FakeProbe(now); probe.adapters[0].fail=True
   provider=BatchedMarketSnapshotProvider(probe,SymbolCatalogProvider(probe))
   work=MonitorWork(11,1,SignalMode.SINGLE_EXCHANGE,'OUTSIDEUSDT',Exchange.OURBIT,TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('95'),PriceSourceType.OURBIT_ONLY,((Exchange.OURBIT,'NATIVE_X'),),now)
   snapshot=await provider.snapshot((work,))
   self.assertEqual(snapshot,{})
   self.assertEqual([a.calls for a in probe.adapters],[1,0,0])
   self.assertEqual([a.instrument_calls for a in probe.adapters],[0,0,0])
  asyncio.run(run())
 def test_invalid_or_stale_selected_tick_skips_single_without_event_decision(self):
  async def run():
   now=NOW; probe=FakeProbe(now); stale=now-timedelta(seconds=30)
   probe.adapters[0].ticks=[PriceTick('ourbit','NATIVE_X','OUTSIDEUSDT',Decimal('111'),stale,'fake')]
   provider=BatchedMarketSnapshotProvider(probe,SymbolCatalogProvider(probe))
   work=MonitorWork(12,1,SignalMode.SINGLE_EXCHANGE,'OUTSIDEUSDT',Exchange.OURBIT,TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('95'),PriceSourceType.OURBIT_ONLY,((Exchange.OURBIT,'NATIVE_X'),),now)
   self.assertEqual(await provider.snapshot((work,)),{})
   probe.adapters[0].ticks=[PriceTick('ourbit','NATIVE_X','OUTSIDEUSDT',Decimal('0'),now,'fake')]
   self.assertEqual(await provider.snapshot((work,)),{})
  asyncio.run(run())
