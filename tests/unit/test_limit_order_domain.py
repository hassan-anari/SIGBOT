from decimal import Decimal
import unittest
from chainura.domain.signal import (TradeDirection, limit_entry_reached,
    validate_limit_entry)
from chainura.errors import InvalidSignalInputError

class LimitOrderDomainTests(unittest.TestCase):
    def test_limit_entry_is_strictly_beyond_current_market(self):
        for direction,entry,current in (
            (TradeDirection.LONG,Decimal("99.99"),Decimal("100")),
            (TradeDirection.SHORT,Decimal("100.01"),Decimal("100"))):
            with self.subTest(direction=direction):validate_limit_entry(direction,entry,current)

    def test_invalid_limit_entry(self):
        for direction,entry,current in (
            (TradeDirection.LONG,Decimal("100"),Decimal("100")),
            (TradeDirection.LONG,Decimal("101"),Decimal("100")),
            (TradeDirection.SHORT,Decimal("100"),Decimal("100")),
            (TradeDirection.SHORT,Decimal("99"),Decimal("100"))):
            with self.subTest(direction=direction,entry=entry):
                with self.assertRaises(InvalidSignalInputError):validate_limit_entry(direction,entry,current)

    def test_limit_values_are_decimal_only(self):
        with self.assertRaises(InvalidSignalInputError):validate_limit_entry(TradeDirection.LONG,99.0,Decimal("100"))

    def test_limit_threshold_is_inclusive_and_directional(self):
        self.assertTrue(limit_entry_reached(TradeDirection.LONG,Decimal("99"),Decimal("99")))
        self.assertTrue(limit_entry_reached(TradeDirection.SHORT,Decimal("101"),Decimal("101")))
        self.assertFalse(limit_entry_reached(TradeDirection.LONG,Decimal("100"),Decimal("99")))
        self.assertFalse(limit_entry_reached(TradeDirection.SHORT,Decimal("100"),Decimal("101")))
