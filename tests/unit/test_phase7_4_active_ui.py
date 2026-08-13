import unittest
from datetime import datetime,timezone
from decimal import Decimal
from chainura.application.active_signals import ActiveSignalView
from chainura.domain.monitoring import MonitorStage
from chainura.domain.signal import SignalLifecycleStatus,TradeDirection
from chainura.telegram.active_signal_routes import PREFIX,cb,_parse,detail_keyboard,clear_cancelled_signal_fsm
from chainura.telegram.states import SignalComment
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import signalist_menu
from chainura.telegram.signal_routes import _TOP_LEVEL
class ActiveSignalUiTests(unittest.TestCase):
 def test_menu_and_top_level_guard(self):
  self.assertIn(texts.MENU_ACTIVE_SIGNALS,_TOP_LEVEL);self.assertTrue(any(b.text==texts.MENU_ACTIVE_SIGNALS for row in signalist_menu().keyboard for b in row))
 def test_callback_namespace_isolated_ascii_and_short(self):
  value=cb('d',123,2);self.assertTrue(value.startswith(PREFIX));self.assertTrue(value.isascii());self.assertLessEqual(len(value.encode()),64);self.assertEqual(_parse(value),('d',(123,2)))
 def test_comment_is_only_on_active_detail_and_cancel_is_preserved(self):
  def view(status):return ActiveSignalView(1,"BTCUSDT",TradeDirection.LONG,Decimal("100"),10,status,(Decimal("110"),) if status is SignalLifecycleStatus.ACTIVE else (),Decimal("90") if status is SignalLifecycleStatus.ACTIVE else None,MonitorStage.PRE_TP1 if status is SignalLifecycleStatus.ACTIVE else None,0,datetime.now(timezone.utc))
  active=[button.text for row in detail_keyboard(view(SignalLifecycleStatus.ACTIVE),1).inline_keyboard for button in row]
  pending=[button.text for row in detail_keyboard(view(SignalLifecycleStatus.PENDING_DETAILS),1).inline_keyboard for button in row]
  self.assertIn(texts.ACTIVE_SIGNAL_COMMENT,active);self.assertIn(texts.ACTIVE_SIGNAL_CANCEL,active)
  self.assertNotIn(texts.ACTIVE_SIGNAL_COMMENT,pending);self.assertIn(texts.ACTIVE_SIGNAL_CANCEL,pending)

class CancellationFsmTests(unittest.IsolatedAsyncioTestCase):
 class State:
  def __init__(self,signal_id):self.data={"comment_signal_id":signal_id};self.value=SignalComment.waiting_text.state;self.cleared=False
  async def get_data(self):return self.data
  async def get_state(self):return self.value
  async def clear(self):self.cleared=True
 async def test_cancelling_same_signal_clears_comment_fsm(self):
  state=self.State(10);await clear_cancelled_signal_fsm(state,10);self.assertTrue(state.cleared)
 async def test_cancelling_other_signal_preserves_comment_fsm(self):
  state=self.State(11);await clear_cancelled_signal_fsm(state,10);self.assertFalse(state.cleared)
