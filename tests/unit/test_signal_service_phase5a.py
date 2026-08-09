import unittest
from decimal import Decimal
from chainura.application.signal_service import ReferencePriceResult,SnapshotRequest,SignalService
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType,SignalMode
from chainura.errors import InconsistentReferencePriceError
class Provider:
 async def get_reference_price(self,**kw):return ReferencePriceResult(Decimal('100'),PriceSourceType.COMPOSITE,((Exchange.OURBIT,Decimal('100')),(Exchange.TOOBIT,Decimal('100'))),True,'BTCUSDT',((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt')))
class SnapshotServiceTests(unittest.IsolatedAsyncioTestCase):
 async def test_capture_requires_coherent_healthy_result(self):
  result=await SignalService(None,Provider()).capture_snapshot(SnapshotRequest(SignalMode.COMPOSITE,'BTCUSDT',None,None))
  self.assertEqual(result.reference_price,Decimal('100'))
 async def test_context_mismatch_is_rejected(self):
  with self.assertRaises(InconsistentReferencePriceError):
   await SignalService(None,Provider()).capture_snapshot(SnapshotRequest(SignalMode.COMPOSITE,'ETHUSDT',None,None))
