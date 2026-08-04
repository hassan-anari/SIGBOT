import unittest
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType,TradeDirection
from chainura.rendering.models import SignalRenderPayload,SignalRenderEventKind
from chainura.errors import InvalidSignalInputError
NOW=datetime(2026,8,4,12,tzinfo=timezone.utc)
def payload(kind,elapsed,rendered=None):
 return SignalRenderPayload(1,kind,Exchange.OURBIT,TradeDirection.LONG,PriceSourceType.COMPOSITE,'BTCUSDT','BTCUSDT','BTC','USDT',Decimal('100'),5,(Decimal('110'),),Decimal('90'),Decimal('100'),Decimal('111'),Decimal('55'),NOW,rendered or NOW+timedelta(seconds=elapsed),elapsed)
class Phase6BPayloadValidationTests(unittest.TestCase):
 def test_initial_elapsed_still_zero(self):
  payload(SignalRenderEventKind.INITIAL,0,NOW)
  with self.assertRaises(InvalidSignalInputError):payload(SignalRenderEventKind.INITIAL,1)
 def test_update_elapsed_positive_nonnegative_and_aware_ordered(self):
  payload(SignalRenderEventKind.TP,3661)
  payload(SignalRenderEventKind.SL,1)
  payload(SignalRenderEventKind.BREAKEVEN,0,NOW)
  with self.assertRaises(InvalidSignalInputError):payload(SignalRenderEventKind.TP,-1,NOW)
  with self.assertRaises(InvalidSignalInputError):payload(SignalRenderEventKind.TP,True,NOW)
  with self.assertRaises(InvalidSignalInputError):payload(SignalRenderEventKind.TP,0,NOW-timedelta(seconds=1))
if __name__=='__main__':unittest.main()
