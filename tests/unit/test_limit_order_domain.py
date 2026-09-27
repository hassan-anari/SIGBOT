from decimal import Decimal
import pytest
from chainura.domain.signal import (TradeDirection, limit_entry_reached,
    validate_limit_entry)
from chainura.errors import InvalidSignalInputError

@pytest.mark.parametrize("direction,entry,current", [
    (TradeDirection.LONG,Decimal("99.99"),Decimal("100")),
    (TradeDirection.SHORT,Decimal("100.01"),Decimal("100")),
])
def test_limit_entry_is_strictly_beyond_current_market(direction,entry,current):
    validate_limit_entry(direction,entry,current)

@pytest.mark.parametrize("direction,entry,current", [
    (TradeDirection.LONG,Decimal("100"),Decimal("100")),
    (TradeDirection.LONG,Decimal("101"),Decimal("100")),
    (TradeDirection.SHORT,Decimal("100"),Decimal("100")),
    (TradeDirection.SHORT,Decimal("99"),Decimal("100")),
])
def test_invalid_limit_entry(direction,entry,current):
    with pytest.raises(InvalidSignalInputError):validate_limit_entry(direction,entry,current)

def test_limit_values_are_decimal_only():
    with pytest.raises(InvalidSignalInputError):validate_limit_entry(TradeDirection.LONG,99.0,Decimal("100"))

def test_limit_threshold_is_inclusive_and_directional():
    assert limit_entry_reached(TradeDirection.LONG,Decimal("99"),Decimal("99"))
    assert limit_entry_reached(TradeDirection.SHORT,Decimal("101"),Decimal("101"))
    assert not limit_entry_reached(TradeDirection.LONG,Decimal("100"),Decimal("99"))
    assert not limit_entry_reached(TradeDirection.SHORT,Decimal("100"),Decimal("101"))
