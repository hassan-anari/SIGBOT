import asyncio
import unittest
from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace

from chainura.application.access import Actor,ActorKind
from chainura.application.market_gateway import SymbolContract,SymbolSearchPage
from chainura.application.signal_service import ReferencePriceResult
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType
from chainura.domain.signalist import Signalist,SignalistStatus
from chainura.telegram import signal_routes
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.signal_callbacks import SignalCallback,signal_callback
from chainura.telegram.states import SignalCreate
from chainura.errors import EntryValidationError
from chainura.telegram import admin_routes

class State:
 def __init__(self): self.state=None; self.data={}
 async def get_state(self): return self.state
 async def set_state(self,v): self.state=v.state if hasattr(v,'state') else v
 async def get_data(self): return dict(self.data)
 async def set_data(self,v): self.data=dict(v)
 async def update_data(self,**v): self.data.update(v)
 async def clear(self): self.state=None; self.data={}
class Message:
 def __init__(self,text=''): self.text=text; self.answers=[]; self.chat=SimpleNamespace(id=1); self.from_user=SimpleNamespace(id=2)
 async def answer(self,text,reply_markup=None): self.answers.append((text,reply_markup))
class Query:
 def __init__(self): self.message=Message(); self.data=''
class Ack:
 def __init__(self): self.calls=[]
 async def answer_once(self,text=None): self.calls.append(text)

def actor(status=SignalistStatus.ACTIVE):
 now=datetime.now(timezone.utc); item=Signalist.rehydrate(id=7,telegram_user_id=2,display_name='S',telegram_username=None,status=status,created_at=now,updated_at=now,archived_at=now if status is SignalistStatus.ARCHIVED else None)
 return Actor({SignalistStatus.ACTIVE:ActorKind.ACTIVE_SIGNALIST,SignalistStatus.INACTIVE:ActorKind.INACTIVE_SIGNALIST,SignalistStatus.ARCHIVED:ActorKind.ARCHIVED_SIGNALIST}[status],item)
class Catalog:
 def __init__(self): self.shared=[SymbolContract('BTCUSDT','BTCUSDT','BTC','USDT',None)]; self.native=[SymbolContract('DOGEUSDT','DOGE_USDT','DOGE','USDT',Exchange.LBANK)]
 async def search_shared(self,q,page=1): return SymbolSearchPage(tuple(self.shared),page,2 if q=='many' else 1)
 async def get_shared(self,s): return next((x for x in self.shared if x.canonical_symbol==s),None)
 async def search_exchange(self,e,q,page=1): return SymbolSearchPage(tuple(self.native),page,2 if q=='many' else 1)
 async def get_exchange_contract(self,e,s): return next((x for x in self.native if x.exchange==e and x.native_symbol==s),None)
class Service:
 def __init__(self): self.validations=0; self.created=[]; self.gate=None
 async def validate_entry(self,c):
  self.validations+=1
  if self.gate: self.gate[0].set(); await self.gate[1].wait()
  if abs(c.entry_price-Decimal("100"))/Decimal("100")*Decimal("100") > Decimal("0.05"):
   raise EntryValidationError("outside")
  source=PriceSourceType.COMPOSITE if c.selected_exchange is None else PriceSourceType.LBANK_ONLY
  prices=((Exchange.OURBIT,Decimal('100')),(Exchange.TOOBIT,Decimal('100'))) if c.selected_exchange is None else ((Exchange.LBANK,Decimal('100')),)
  maps=((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt')) if c.selected_exchange is None else ((Exchange.LBANK,c.native_symbol),)
  return ReferencePriceResult(Decimal('100'),source,prices,True,c.canonical_symbol,maps)
 async def create_validated(self,c,r): self.created.append(c); return SimpleNamespace(id=1)

class WizardTests(unittest.IsolatedAsyncioTestCase):
 async def cb(self,state,kind,ref='-',page=0,catalog=None,service=None,flow=None):
  q=Query(); a=Ack(); data=SignalCallback(kind=kind,flow=flow or state.data['flow_token'],ref=ref,page=page)
  await signal_routes.signal_callback_handler(q,data,actor(),state,a,catalog or Catalog(),service or Service())
  self.assertEqual(len(a.calls),1); return q
 async def start(self):
  state=State(); message=Message(texts.MENU_CREATE_SIGNAL); await signal_routes.start_signal(message,actor(),state)
  self.assertEqual(state.state,SignalCreate.shared_search.state); self.assertNotIn(state.data['creation_token'],message.answers[-1][0]); return state
 async def test_complete_shared_wizard_and_no_destination_disclosure(self):
  state=await self.start(); catalog=Catalog(); service=Service()
  await signal_routes.shared_query(Message('BTC'),actor(),state,catalog)
  await self.cb(state,'ss','0',1,catalog,service)
  await self.cb(state,'dir','long',catalog=catalog,service=service)
  await signal_routes.entry_input(Message('100.05'),actor(),state,service)
  await signal_routes.leverage_input(Message('10'),actor(),state)
  await signal_routes.tp_input(Message('101'),actor(),state); await signal_routes.tp_input(Message('102'),actor(),state)
  await self.cb(state,'tp','finish',catalog=catalog,service=service)
  await signal_routes.sl_input(Message('99'),actor(),state)
  review=state.data.copy(); self.assertEqual(state.state,SignalCreate.review.state)
  self.assertNotIn('مقصد',texts.signal_review(review))
  result=await self.cb(state,'confirm',catalog=catalog,service=service)
  self.assertEqual(len(service.created),1); self.assertIsNone(state.state); self.assertEqual(result.message.answers[-1][0],texts.SIGNAL_RECORDED_SUCCESS)
 async def test_outside_exchange_native_pagination_and_short(self):
  state=await self.start(); c=Catalog(); s=Service(); await self.cb(state,'out',catalog=c,service=s); await self.cb(state,'ex','lbank',catalog=c,service=s)
  await signal_routes.native_query(Message('many'),actor(),state,c); await self.cb(state,'np',page=2,catalog=c,service=s)
  await self.cb(state,'ns','0',2,c,s); await self.cb(state,'dir','short',catalog=c,service=s)
  self.assertEqual(state.data['native_symbol'],'DOGE_USDT'); self.assertEqual(state.state,SignalCreate.entry.state)
 async def test_empty_finish_invalid_tp_sl_and_parsers(self):
  state=await self.start(); state.data.update(mode='composite',canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',direction='long',entry_price='100',leverage=2,take_profits=[]); state.state=SignalCreate.take_profits.state
  q=await self.cb(state,'tp','finish'); self.assertEqual(q.message.answers[-1][0],texts.SIGNAL_FINISH_TP_BLOCKED)
  m=Message('99'); await signal_routes.tp_input(m,actor(),state); self.assertEqual(m.answers[-1][0],texts.SIGNAL_INVALID_DIRECTIONAL_PRICE)
  state.data['take_profits']=['101']; state.state=SignalCreate.stop_loss.state; m=Message('101'); await signal_routes.sl_input(m,actor(),state); self.assertEqual(m.answers[-1][0],texts.SIGNAL_INVALID_DIRECTIONAL_PRICE)
 async def test_stale_cross_flow_and_page_callbacks_are_ignored(self):
  state=await self.start(); original=state.state; await self.cb(state,'out',flow='wrong'); self.assertEqual(state.state,original)
  await signal_routes.shared_query(Message('BTC'),actor(),state,Catalog()); await self.cb(state,'ss','0',2); self.assertEqual(state.state,SignalCreate.shared_search.state)
 async def test_cancel_during_entry_validation_does_not_resurrect(self):
  state=await self.start(); state.data.update(mode='composite',canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,native_symbol=None,direction='long'); state.state=SignalCreate.entry.state
  service=Service(); arrived=asyncio.Event(); release=asyncio.Event(); service.gate=(arrived,release)
  task=asyncio.create_task(signal_routes.entry_input(Message('100'),actor(),state,service)); await arrived.wait(); await self.cb(state,'cancel',service=service); release.set(); await task
  self.assertIsNone(state.state)
 async def test_cancel_during_final_validation_prevents_persistence(self):
  state=await self.start(); state.data.update(mode='composite',canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,native_symbol=None,direction='long',entry_price='100',leverage=2,take_profits=['101'],stop_loss='99',reference_price='100',price_source_type='composite'); state.state=SignalCreate.review.state
  service=Service(); arrived=asyncio.Event(); release=asyncio.Event(); service.gate=(arrived,release)
  task=asyncio.create_task(self.cb(state,'confirm',service=service)); await arrived.wait(); await self.cb(state,'cancel',service=service); release.set(); await task
  self.assertEqual(service.created,[]); self.assertIsNone(state.state)
 async def test_two_concurrent_confirmations_persist_at_most_once(self):
  state=await self.start(); state.data.update(mode='composite',canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,native_symbol=None,direction='long',entry_price='100',leverage=2,take_profits=['101'],stop_loss='99',reference_price='100',price_source_type='composite'); state.state=SignalCreate.review.state
  service=Service(); arrived=asyncio.Event(); release=asyncio.Event(); count=0
  original=service.validate_entry
  async def gated(command):
   nonlocal count
   count+=1
   if count==2: arrived.set()
   await release.wait(); return await original(command)
  service.validate_entry=gated
  first=asyncio.create_task(self.cb(state,'confirm',service=service)); second=asyncio.create_task(self.cb(state,'confirm',service=service))
  await arrived.wait(); release.set(); await asyncio.gather(first,second)
  self.assertLessEqual(len(service.created),1); self.assertIsNone(state.state)
 async def test_inactive_and_archived_cannot_start(self):
  for status in (SignalistStatus.INACTIVE,SignalistStatus.ARCHIVED):
   state=State(); m=Message(); await signal_routes.start_signal(m,actor(status),state); self.assertIsNone(state.state)
 async def test_exact_tolerance_boundary_and_above_boundary(self):
  for value,valid in (("100.05",True),("100.0501",False)):
   state=await self.start(); state.data.update(mode='composite',canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,native_symbol=None,direction='long'); state.state=SignalCreate.entry.state
   message=Message(value); await signal_routes.entry_input(message,actor(),state,Service())
   self.assertEqual(state.state,SignalCreate.leverage.state if valid else SignalCreate.entry.state)
 async def test_start_and_main_menu_invalidate_flow(self):
  state=await self.start(); old=state.data['flow_token']; await admin_routes.global_start(Message('/start'),actor(),state); self.assertIsNone(state.state)
  state=await self.start(); state.state=SignalCreate.native_search.state; await admin_routes.global_main(Message(texts.MENU_MAIN),actor(),state); self.assertIsNone(state.state); self.assertNotEqual(old,state.data.get('flow_token'))

class CallbackSchemaTests(unittest.TestCase):
 def test_ascii_typed_and_bounded(self):
  value=signal_callback('confirm','abcdef1234'); self.assertTrue(value.isascii()); self.assertLessEqual(len(value.encode()),64)
  parsed=SignalCallback.unpack(value); self.assertEqual(parsed.kind,'confirm')
