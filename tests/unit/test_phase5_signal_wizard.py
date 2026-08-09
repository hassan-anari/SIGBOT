import unittest
from chainura.telegram.states import SignalCreate
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.signal_callbacks import signal_callback,SignalCallback

class Phase71WizardContractTests(unittest.TestCase):
 def test_manual_entry_state_and_prompt_are_absent(self):
  self.assertFalse(hasattr(SignalCreate,'entry'))
  self.assertFalse(hasattr(texts,'SIGNAL_ASK_ENTRY'))
 def test_continue_callback_is_typed_ascii_and_bounded(self):
  packed=signal_callback('continue','resume','42')
  parsed=SignalCallback.unpack(packed)
  self.assertEqual((parsed.kind,parsed.ref),('continue','42'))
 def test_new_user_facing_copy_is_persian(self):
  self.assertIn('۰٫۷۰٪',texts.SIGNAL_DRIFT_EXCEEDED)
  self.assertEqual(texts.SIGNAL_CONTINUE_DETAILS,'ادامه ثبت اطلاعات')
  self.assertNotIn('published',texts.SIGNAL_INITIAL_QUEUED.lower())
