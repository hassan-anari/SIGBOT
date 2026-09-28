import unittest
from chainura.telegram.states import SignalCreate
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.signal_callbacks import signal_callback,SignalCallback
from chainura.telegram.presentation.keyboards import signal_order_types

class Phase71WizardContractTests(unittest.TestCase):
 def test_market_manual_entry_state_and_prompt_remain_absent(self):
  self.assertFalse(hasattr(SignalCreate,'entry'))
  self.assertFalse(hasattr(texts,'SIGNAL_ASK_ENTRY'))
 def test_limit_is_an_explicit_creation_choice_with_own_entry_state(self):
  self.assertTrue(hasattr(SignalCreate,'order_type'));self.assertTrue(hasattr(SignalCreate,'limit_entry'))
  labels=[button.text for row in signal_order_types('flow').inline_keyboard for button in row]
  self.assertIn(texts.SIGNAL_ORDER_MARKET,labels);self.assertIn(texts.SIGNAL_ORDER_LIMIT,labels)
  review=texts.signal_review({'order_type':'limit','canonical_symbol':'BTCUSDT',
   'direction':'long','entry_price':'90','leverage':10,'take_profits':['95'],
   'stop_loss':'85'})
  self.assertIn(texts.SIGNAL_ORDER_LIMIT,review);self.assertIn('ورود لیمیت',review)
 def test_continue_callback_is_typed_ascii_and_bounded(self):
  packed=signal_callback('continue','resume','42')
  parsed=SignalCallback.unpack(packed)
  self.assertEqual((parsed.kind,parsed.ref),('continue','42'))
 def test_new_user_facing_copy_is_persian(self):
  self.assertIn('۰٫۷۰٪',texts.SIGNAL_DRIFT_EXCEEDED)
  self.assertEqual(texts.SIGNAL_CONTINUE_DETAILS,'ادامه ثبت اطلاعات')
  self.assertNotIn('published',texts.SIGNAL_INITIAL_QUEUED.lower())
