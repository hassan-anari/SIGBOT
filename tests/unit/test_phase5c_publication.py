import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from chainura.application.initial_caption import build_initial_caption, TELEGRAM_PHOTO_CAPTION_MAX_LENGTH
from chainura.domain.destination import Exchange
from chainura.domain.publication import select_initial_exchange, tehran_local_date
from chainura.domain.signal import SignalMode, TradeDirection
from chainura.errors import CaptionGenerationError

class CaptionTests(unittest.TestCase):
 def signal(self,direction=TradeDirection.LONG,tps=(Decimal("1.2300"),),symbol="BTCUSDT"):
  return SimpleNamespace(direction=direction,take_profits=tps,canonical_symbol=symbol,
   entry_price=Decimal("1.00000001"),leverage=25,stop_loss=Decimal("0.99999999"))
 def test_long_private_exact_and_bidi(self):
  caption=build_initial_caption(self.signal(),private=True)
  self.assertIn("لانگ",caption);self.assertIn("#پیامک",caption)
  self.assertIn("\u2066<code>1.00000001</code>\u2069",caption)
  self.assertIn("BTCUSDT",caption);self.assertNotIn("reference",caption.lower())
 def test_short_public_tp_order(self):
  signal=self.signal(TradeDirection.SHORT,(Decimal("0.9"),Decimal("0.80")))
  signal.stop_loss=Decimal("1.1")
  caption=build_initial_caption(signal,private=False)
  self.assertIn("شورت",caption);self.assertNotIn("#پیامک",caption)
  self.assertLess(caption.index("0.9"),caption.index("0.80"))
 def test_html_escape(self):
  caption=build_initial_caption(self.signal(symbol="A&B"),private=False)
  self.assertIn("A&amp;B",caption);self.assertNotIn("A&B",caption)
 def test_caption_limit(self):
  with self.assertRaises(CaptionGenerationError):
   build_initial_caption(self.signal(tps=tuple(Decimal(f"1.{i:01000d}") for i in range(2))),private=False)

class PlanningTests(unittest.TestCase):
 def test_composite_priority_and_degraded_sources(self):
  self.assertEqual(select_initial_exchange(SignalMode.COMPOSITE,None,
   (Exchange.OURBIT,Exchange.TOOBIT,Exchange.LBANK),(Exchange.TOOBIT,Exchange.LBANK)),Exchange.TOOBIT)
 def test_single_compatibility_and_none(self):
  self.assertEqual(select_initial_exchange(SignalMode.SINGLE_EXCHANGE,Exchange.LBANK,
   (Exchange.OURBIT,Exchange.LBANK),(Exchange.LBANK,)),Exchange.LBANK)
  self.assertIsNone(select_initial_exchange(SignalMode.SINGLE_EXCHANGE,Exchange.LBANK,
   (Exchange.OURBIT,),(Exchange.LBANK,)))
 def test_tehran_rollover(self):
  self.assertEqual(str(tehran_local_date(datetime(2026,1,1,20,31,tzinfo=timezone.utc))),"2026-01-02")
