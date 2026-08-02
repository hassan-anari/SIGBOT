import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from chainura.domain.destination import Exchange
from chainura.domain.signal import *
from chainura.errors import InvalidSignalInputError

NOW=datetime(2026,8,1,tzinfo=timezone.utc)
def signal(direction=TradeDirection.LONG, tps=(Decimal('101'),), sl=Decimal('99'), **kw):
 return Signal(None,1,'opaque',kw.get('mode',SignalMode.COMPOSITE),'BTCUSDT','BTC','USDT',kw.get('exchange'),direction,Decimal('100'),3,tps,sl,Decimal('100'),kw.get('source',PriceSourceType.COMPOSITE),SignalLifecycleStatus.ACTIVE,kw.get('maps',((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt'))),NOW,NOW)
class SignalDomainTests(unittest.TestCase):
 def test_valid_long_short_and_immutable(self):
  a=signal(); b=signal(TradeDirection.SHORT,(Decimal('99'),Decimal('98')),Decimal('101'))
  self.assertEqual(a.take_profits,(Decimal('101'),)); self.assertEqual(b.direction,TradeDirection.SHORT)
  with self.assertRaises(FrozenInstanceError): a.leverage=4
 def test_directional_rules(self):
  for args in [(TradeDirection.LONG,(Decimal('102'),Decimal('101')),Decimal('99')),(TradeDirection.LONG,(Decimal('101'),),Decimal('101')),(TradeDirection.SHORT,(Decimal('98'),Decimal('99')),Decimal('101')),(TradeDirection.SHORT,(Decimal('99'),),Decimal('99'))]:
   with self.assertRaises(InvalidSignalInputError): signal(*args)
 def test_empty_duplicates_positive_decimal(self):
  for tps in [(),(Decimal('101'),Decimal('101')),(Decimal('-1'),)]:
   with self.assertRaises(InvalidSignalInputError): signal(tps=tps)
  with self.assertRaises(InvalidSignalInputError): signal(tps=(101,))
 def test_mode_invariants(self):
  s=signal(mode=SignalMode.SINGLE_EXCHANGE,exchange=Exchange.OURBIT,source=PriceSourceType.OURBIT_ONLY,maps=((Exchange.OURBIT,'BTCUSDT'),))
  self.assertEqual(s.selected_exchange,Exchange.OURBIT)
  with self.assertRaises(InvalidSignalInputError): signal(mode=SignalMode.SINGLE_EXCHANGE)
 def test_parsers_and_tolerance(self):
  self.assertEqual(parse_price(' 0.0010 '),Decimal('0.0010')); self.assertEqual(parse_leverage('123'),123)
  for value in ['۱','1,000','1e2','+1','-1','0','NaN','Infinity','.1','1.']:
   with self.assertRaises(InvalidSignalInputError): parse_price(value)
  for value in ['۱','0','-1','1.0','1e2','+1']:
   with self.assertRaises(InvalidSignalInputError): parse_leverage(value)
  self.assertTrue(entry_is_within_tolerance(Decimal('100.05'),Decimal('100')))
  self.assertFalse(entry_is_within_tolerance(Decimal('100.0500001'),Decimal('100')))
