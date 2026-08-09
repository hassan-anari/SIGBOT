import unittest
from datetime import datetime,timezone
from decimal import Decimal
from chainura.domain.destination import Exchange
from chainura.domain.signal import *
from chainura.errors import InvalidSignalInputError

class StagedSignalDomainTests(unittest.TestCase):
 def base(self,status=SignalLifecycleStatus.PENDING_DETAILS,tps=(),sl=None):
  now=datetime.now(timezone.utc)
  return Signal(None,1,'token',SignalMode.COMPOSITE,'BTCUSDT','BTC','USDT',None,TradeDirection.LONG,Decimal('100'),10,tps,sl,Decimal('100'),PriceSourceType.COMPOSITE,status,((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt')),now,now,((Exchange.OURBIT,Decimal('100.7')),(Exchange.TOOBIT,Decimal('100.6'))))
 def test_decimal_drift_boundary_and_p0_denominator(self):
  self.assertEqual(initial_entry_drift_percent(Decimal('100'),Decimal('100.70')),Decimal('0.700'))
  self.assertTrue(initial_entry_drift_is_acceptable(Decimal('100'),Decimal('100.70')))
  self.assertFalse(initial_entry_drift_is_acceptable(Decimal('100'),Decimal('100.7001')))
 def test_pending_has_no_levels_but_active_stays_strict(self):
  self.base()
  with self.assertRaises(InvalidSignalInputError):self.base(SignalLifecycleStatus.ACTIVE)
  with self.assertRaises(InvalidSignalInputError):self.base(SignalLifecycleStatus.ACTIVE,(Decimal('101'),),None)
  active=self.base(SignalLifecycleStatus.ACTIVE,(Decimal('101'),Decimal('102')),Decimal('99'))
  self.assertEqual(active.entry_price,Decimal('100'))
