import unittest
from decimal import Decimal
from chainura.application.update_caption import UpdateCaptionInput, build_update_caption, UpdateCaptionError
from chainura.domain.monitoring import SignalEventKind
class UpdateCaptionTests(unittest.TestCase):
 def test_tp_caption_is_persian_bidi_and_private_tag_free(self):
  text=build_update_caption(UpdateCaptionInput(SignalEventKind.TP_HIT,'BTCUSDT',Decimal('123.4500'),Decimal('12.30'),1))
  self.assertIn('تارگت',text);self.assertIn('\u2066BTCUSDT\u2069',text);self.assertNotIn('بازده اهرمی',text);self.assertNotIn('#پیامک',text);self.assertNotIn('ارسال به مقصدها',text)
 def test_tp2_sl_breakeven_and_html_escape(self):
  self.assertIn('تارگت \u20662\u2069',build_update_caption(UpdateCaptionInput(SignalEventKind.TP_HIT,'ETH<USDT>',Decimal('1.00000001'),Decimal('-2.5'),2)))
  self.assertIn('حد ضرر',build_update_caption(UpdateCaptionInput(SignalEventKind.SL_HIT,'ETHUSDT',Decimal('9'),Decimal('-10'))))
  self.assertNotIn('نتیجه:',build_update_caption(UpdateCaptionInput(SignalEventKind.BREAKEVEN,'ETHUSDT',Decimal('9'),Decimal('99'))))
  self.assertIn('ETH&lt;USDT&gt;',build_update_caption(UpdateCaptionInput(SignalEventKind.TP_HIT,'ETH<USDT>',Decimal('1'),Decimal('1'),3)))
 def test_caption_length_rejection(self):
  with self.assertRaises(UpdateCaptionError):build_update_caption(UpdateCaptionInput(SignalEventKind.TP_HIT,'X'*2000,Decimal('1'),Decimal('1'),1))
if __name__=='__main__':unittest.main()
