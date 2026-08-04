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
