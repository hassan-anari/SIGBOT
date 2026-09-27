from datetime import datetime, timezone
from decimal import Decimal
import unittest

from chainura.application.message_templates import BUILTIN_TEMPLATES, position_action_context
from chainura.application.publication import UpdateContentType, update_content_type
from chainura.domain.message_template import DestinationMessageKind
from chainura.domain.monitoring import (MarketDecision, MonitorStage, SignalEventKind,
    evaluate_price)
from chainura.domain.position import (PositionActionKind, apply_position_action,
    initial_position)
from chainura.domain.signal import PriceSourceType, TradeDirection
from chainura.telegram.event_routes import tp_action_keyboard

NOW=datetime(2026,9,27,tzinfo=timezone.utc)

class Phase4BPositionTests(unittest.TestCase):
 def test_save50_and_risk_free_are_independent_and_weighted(self):
  state=initial_position(Decimal('90'))
  saved=apply_position_action(state,PositionActionKind.SAVE_PROFIT_50,leveraged_roi=Decimal('20')).state
  self.assertEqual(saved.remaining_fraction,Decimal('.5'));self.assertEqual(saved.realized_weighted_roi,Decimal('10'))
  risk_free=apply_position_action(saved,PositionActionKind.RISK_FREE,entry_price=Decimal('100')).state
  self.assertTrue(risk_free.risk_free_active);self.assertTrue(risk_free.save_profit_50_used)
  closed=apply_position_action(risk_free,PositionActionKind.RISK_FREE_HIT_CLOSE,leveraged_roi=Decimal('0'),occurred_at=NOW).state
  self.assertEqual(closed.realized_weighted_roi,Decimal('10'));self.assertTrue(closed.is_closed)

 def test_post_tp_sl_and_risk_free_threshold_crossing(self):
  market=lambda price:MarketDecision(Decimal(price),(),PriceSourceType.COMPOSITE,NOW)
  sl=evaluate_price(direction=TradeDirection.LONG,entry=Decimal('100'),leverage=10,take_profits=(Decimal('110'),Decimal('120')),stop_loss=Decimal('90'),next_tp_index=2,stage=MonitorStage.POST_TP1,market=market('89'))
  self.assertEqual(sl[0].kind,SignalEventKind.SL_HIT)
  rf=evaluate_price(direction=TradeDirection.LONG,entry=Decimal('100'),leverage=10,take_profits=(Decimal('110'),Decimal('120')),stop_loss=Decimal('90'),next_tp_index=2,stage=MonitorStage.POST_TP1,market=market('95'),risk_free_active=True,effective_stop_price=Decimal('100'))
  self.assertEqual(rf[0].kind,SignalEventKind.RISK_FREE_HIT);self.assertEqual(rf[0].leveraged_roi,Decimal('0'))

 def test_action_keyboard_availability_is_explicit(self):
  actions=(PositionActionKind.NORMAL_TP,PositionActionKind.RISK_FREE,PositionActionKind.SAVE_PROFIT_50,PositionActionKind.FULL_CLOSE)
  keyboard=tp_action_keyboard(7,actions)
  self.assertEqual(len(keyboard.inline_keyboard),4)
  self.assertNotIn('مقصد',str(keyboard.model_dump()))
  final=tp_action_keyboard(7,(PositionActionKind.NORMAL_TP,PositionActionKind.FULL_CLOSE))
  self.assertEqual(len(final.inline_keyboard),2)

 def test_risk_free_hit_is_text_and_templates_exist(self):
  self.assertIs(update_content_type(SignalEventKind.RISK_FREE_HIT),UpdateContentType.TEXT)
  for kind in (DestinationMessageKind.RISK_FREE_ACTIVATED,DestinationMessageKind.SAVE_PROFIT_50,DestinationMessageKind.FULL_CLOSE,DestinationMessageKind.RISK_FREE_HIT):
   self.assertTrue(BUILTIN_TEMPLATES[kind])

if __name__=='__main__':unittest.main()
