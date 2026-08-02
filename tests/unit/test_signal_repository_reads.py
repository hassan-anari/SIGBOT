import unittest
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType,SignalLifecycleStatus,SignalMode,TradeDirection
from chainura.errors import SignalCreationError
from chainura.persistence.signal_repository import SqlAlchemySignalRepository
class Result:
 def __init__(self,rows): self.rows=rows
 def one_or_none(self): return self.rows[0] if self.rows else None
 def all(self): return self.rows
class Session:
 def __init__(self,row,fail_child=None): self.row=row; self.calls=0; self.fail_child=fail_child
 async def get(self,*args): return self.row
 async def scalars(self,statement):
  self.calls+=1
  if self.fail_child==self.calls: raise RuntimeError('raw storage failure')
  if self.calls==1: return Result([SimpleNamespace(price=Decimal('101'))])
  return Result([SimpleNamespace(exchange=Exchange.OURBIT,native_symbol='BTCUSDT'),SimpleNamespace(exchange=Exchange.TOOBIT,native_symbol='BTCUSDT'),SimpleNamespace(exchange=Exchange.LBANK,native_symbol='btc_usdt')])
def row(**changes):
 now=datetime.now(timezone.utc); values=dict(id=1,signalist_id=1,creation_token='t',mode=SignalMode.COMPOSITE,canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,direction=TradeDirection.LONG,entry_price=Decimal('100'),leverage=2,stop_loss=Decimal('99'),reference_price=Decimal('100'),price_source_type=PriceSourceType.COMPOSITE,status=SignalLifecycleStatus.ACTIVE,created_at=now,updated_at=now); values.update(changes); return SimpleNamespace(**values)
class RepositoryReadFailureTests(unittest.IsolatedAsyncioTestCase):
 async def test_tp_child_failure_is_sanitized(self):
  with self.assertRaises(SignalCreationError): await SqlAlchemySignalRepository(Session(row(),1)).get_by_id(1)
 async def test_exchange_child_failure_is_sanitized(self):
  with self.assertRaises(SignalCreationError): await SqlAlchemySignalRepository(Session(row(),2)).get_by_id(1)
 async def test_malformed_aggregate_is_sanitized(self):
  with self.assertRaises(SignalCreationError): await SqlAlchemySignalRepository(Session(row(leverage=0))).get_by_id(1)
 async def test_creation_token_child_failure_is_sanitized(self):
  class TokenSession(Session):
   async def scalars(self,statement):
    self.calls+=1
    if self.calls==1: return Result([self.row])
    raise RuntimeError('raw child failure')
  with self.assertRaises(SignalCreationError): await SqlAlchemySignalRepository(TokenSession(row())).get_by_creation_token('t')
