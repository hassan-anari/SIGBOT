import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType,TradeDirection
from chainura.rendering import *
NOW=datetime(2026,8,1,tzinfo=timezone.utc)
def payload(): return SignalRenderPayload(1,SignalRenderEventKind.INITIAL,Exchange.OURBIT,TradeDirection.LONG,PriceSourceType.COMPOSITE,'BTCUSDT','BTCUSDT','BTC','USDT',Decimal('1.00'),2,(Decimal('1.1'),),Decimal('.9'),Decimal('1.01'),Decimal('1.01'),Decimal('0.00'),NOW,NOW,0)
class RenderingTests(unittest.TestCase):
 def tearDown(self): unregister_signal_renderer(Exchange.OURBIT)
 def test_immutable_payload_and_missing_renderer(self):
  p=payload()
  with self.assertRaises(FrozenInstanceError): p.leverage=4
  with self.assertRaises(RendererNotConfiguredError): render_signal_image(p,'a','b')
 def test_dispatch_passes_payload_unchanged(self):
  seen=[]
  def renderer(p,t,o): seen.append((p,t,o)); return RenderedSignalImage(o,1,2,'image/png','abc')
  register_signal_renderer(Exchange.OURBIT,renderer); p=payload(); out=render_signal_image(p,'template','output')
  self.assertIs(seen[0][0],p); self.assertEqual(out.file_path,Path('output'))
 def test_time_strategies(self):
  self.assertEqual(TIME_DISPLAY_STRATEGIES[Exchange.OURBIT],TimeDisplayStrategy.ELAPSED)
  self.assertEqual(TIME_DISPLAY_STRATEGIES[Exchange.TOOBIT],TimeDisplayStrategy.TEHRAN_WALL_CLOCK)
  self.assertEqual(TIME_DISPLAY_STRATEGIES[Exchange.LBANK],TimeDisplayStrategy.UTC_WALL_CLOCK)
