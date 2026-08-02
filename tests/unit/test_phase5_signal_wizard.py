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
from chainura.telegram.phase5_coordination import (FinalizationClaim,
    identity_from_message, phase5_coordination)
from chainura.errors import (EntryOutsideToleranceError,
    InconsistentReferencePriceError, ReferencePriceUnavailableError,
    SignalCreationError, SymbolCatalogError)
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
 def __init__(self,user_id=2):
  self.message=Message(); self.message.from_user=SimpleNamespace(id=999,is_bot=True)
  self.from_user=SimpleNamespace(id=user_id,is_bot=False); self.data=''
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
class EmptyCatalog(Catalog):
 async def search_shared(self,q,page=1): return SymbolSearchPage((),1,1)
 async def search_exchange(self,e,q,page=1): return SymbolSearchPage((),1,1)
class FailingCatalog(Catalog):
 async def search_shared(self,q,page=1): raise SymbolCatalogError('controlled')
 async def search_exchange(self,e,q,page=1): raise SymbolCatalogError('controlled')
class BlockingFailCatalog(Catalog):
 def __init__(self,operation):
  super().__init__(); self.operation=operation; self.arrived=asyncio.Event(); self.release=asyncio.Event()
 async def _fail(self): self.arrived.set(); await self.release.wait(); raise SymbolCatalogError('controlled')
 async def search_shared(self,q,page=1):
  if self.operation=='sp': return await self._fail()
  return await super().search_shared(q,page)
 async def search_exchange(self,e,q,page=1):
  if self.operation=='np': return await self._fail()
  return await super().search_exchange(e,q,page)
 async def get_shared(self,s):
  if self.operation=='ss': return await self._fail()
  return await super().get_shared(s)
 async def get_exchange_contract(self,e,s):
  if self.operation=='ns': return await self._fail()
  return await super().get_exchange_contract(e,s)
class Service:
 def __init__(self): self.validations=0; self.created=[]; self.gate=None; self.stored=None; self.read_error=None
 async def validate_entry(self,c):
  self.validations+=1
  if self.gate: self.gate[0].set(); await self.gate[1].wait()
  if abs(c.entry_price-Decimal("100"))/Decimal("100")*Decimal("100") > Decimal("0.05"):
   raise EntryOutsideToleranceError("outside")
  source=PriceSourceType.COMPOSITE if c.selected_exchange is None else PriceSourceType.LBANK_ONLY
  prices=((Exchange.OURBIT,Decimal('100')),(Exchange.TOOBIT,Decimal('100'))) if c.selected_exchange is None else ((Exchange.LBANK,Decimal('100')),)
  maps=((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'btc_usdt')) if c.selected_exchange is None else ((Exchange.LBANK,c.native_symbol),)
  return ReferencePriceResult(Decimal('100'),source,prices,True,c.canonical_symbol,maps)
 async def create_validated(self,c,r): self.created.append(c); return SimpleNamespace(id=1)
 async def get_by_creation_token(self,token,signalist_id):
  if self.read_error: raise self.read_error
  return self.stored
class BlockingPersistenceService(Service):
 def __init__(self,fail=False): super().__init__(); self.persist_arrived=asyncio.Event(); self.persist_release=asyncio.Event(); self.fail=fail
 async def create_validated(self,c,r):
  self.persist_arrived.set(); await self.persist_release.wait()
  if self.fail: raise SignalCreationError('controlled')
  self.created.append(c); return SimpleNamespace(id=1)
class BlockingInactiveService(BlockingPersistenceService):
 async def create_validated(self,c,r):
  from chainura.errors import SignalistCannotCreateSignalError
  self.persist_arrived.set(); await self.persist_release.wait(); raise SignalistCannotCreateSignalError('controlled')

class WizardTests(unittest.IsolatedAsyncioTestCase):
 async def cb(self,state,kind,ref='-',page=0,catalog=None,service=None,flow=None,actor_value=None):
  q=Query(); a=Ack(); data=SignalCallback(kind=kind,flow=flow or state.data['flow_token'],ref=ref,page=page)
  await signal_routes.signal_callback_handler(q,data,actor_value or actor(),state,a,catalog or Catalog(),service or Service())
  self.assertEqual(len(a.calls),1); return q
 async def start(self):
  state=State(); message=Message(texts.MENU_CREATE_SIGNAL); await signal_routes.start_signal(message,actor(),state)
  self.assertEqual(state.state,SignalCreate.shared_search.state); self.assertNotIn(state.data['creation_token'],message.answers[-1][0]); return state
 async def review(self):
  state=await self.start(); state.data.update(mode='composite',canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,native_symbol=None,direction='long',entry_price='100',leverage=2,take_profits=['101'],stop_loss='99',reference_price='100',price_source_type='composite'); state.state=SignalCreate.review.state; return state
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
  rendered=texts.signal_review(review)
  self.assertNotIn('مقصد',rendered); self.assertIn('مرجع مشترک',rendered)
  self.assertNotIn('composite',rendered); self.assertNotIn('_only',rendered)
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
 async def test_empty_shared_result_exposes_and_enters_outside_flow(self):
  state=await self.start(); message=Message('none'); await signal_routes.shared_query(message,actor(),state,EmptyCatalog())
  labels=_labels(message.answers[-1][1]); self.assertIn(texts.SIGNAL_SEARCH_OUTSIDE,labels); self.assertIn(texts.SIGNAL_CANCEL,labels)
  await self.cb(state,'out',catalog=EmptyCatalog()); self.assertEqual(state.state,SignalCreate.exchange_selection.state)
 async def test_initial_shared_screen_exposes_outside_flow(self):
  state=State(); message=Message(texts.MENU_CREATE_SIGNAL); await signal_routes.start_signal(message,actor(),state)
  self.assertIn(texts.SIGNAL_SEARCH_OUTSIDE,_labels(message.answers[-1][1])); self.assertIn(texts.SIGNAL_CANCEL,_labels(message.answers[-1][1]))
 async def test_native_screens_always_offer_both_back_paths_and_cancel(self):
  for catalog in (EmptyCatalog(),FailingCatalog()):
   state=await self.start(); await self.cb(state,'out',catalog=catalog); prompt=await self.cb(state,'ex','lbank',catalog=catalog)
   self.assertEqual(set(_labels(prompt.message.answers[-1][1])),{texts.SIGNAL_BACK_EXCHANGE,texts.SIGNAL_BACK_SHARED,texts.SIGNAL_CANCEL})
   message=Message('none'); await signal_routes.native_query(message,actor(),state,catalog)
   self.assertTrue({texts.SIGNAL_BACK_EXCHANGE,texts.SIGNAL_BACK_SHARED,texts.SIGNAL_CANCEL}.issubset(_labels(message.answers[-1][1])))
  state=await self.start(); c=Catalog(); await self.cb(state,'out',catalog=c); await self.cb(state,'ex','lbank',catalog=c); message=Message('DOGE'); await signal_routes.native_query(message,actor(),state,c)
  self.assertTrue({texts.SIGNAL_BACK_EXCHANGE,texts.SIGNAL_BACK_SHARED,texts.SIGNAL_CANCEL}.issubset(_labels(message.answers[-1][1])))
  await self.cb(state,'back','exchange',catalog=c); self.assertEqual(state.state,SignalCreate.exchange_selection.state)
  await self.cb(state,'ex','lbank',catalog=c); await self.cb(state,'back','native-shared',catalog=c); self.assertEqual(state.state,SignalCreate.shared_search.state)
 async def test_typed_initial_entry_errors_have_distinct_copy(self):
  for error,copy in ((EntryOutsideToleranceError('x'),texts.ENTRY_OUT_OF_TOLERANCE),(ReferencePriceUnavailableError('x'),texts.REFERENCE_UNAVAILABLE),(InconsistentReferencePriceError('x'),texts.TEMPORARY_ERROR)):
   state=await self.start(); state.data.update(mode='composite',canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,native_symbol=None,direction='long'); state.state=SignalCreate.entry.state
   service=ErrorService(error); message=Message('100'); await signal_routes.entry_input(message,actor(),state,service)
   self.assertEqual(message.answers[-1][0],copy); self.assertEqual(state.state,SignalCreate.entry.state)
 async def test_typed_final_errors_stay_in_review_and_never_persist(self):
  for error,copy in ((EntryOutsideToleranceError('x'),texts.FINAL_ENTRY_OUT_OF_TOLERANCE),(ReferencePriceUnavailableError('x'),texts.REFERENCE_UNAVAILABLE),(InconsistentReferencePriceError('x'),texts.TEMPORARY_ERROR)):
   state=await self.start(); state.data.update(mode='composite',canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,native_symbol=None,direction='long',entry_price='100',leverage=2,take_profits=['101'],stop_loss='99',reference_price='100',price_source_type='composite'); state.state=SignalCreate.review.state
   service=ErrorService(error); query=await self.cb(state,'confirm',service=service)
   self.assertEqual(query.message.answers[-1][0],copy); self.assertEqual(state.state,SignalCreate.review.state); self.assertEqual(service.created,[])
 async def test_all_price_sources_are_localized_in_review(self):
  base=dict(mode='composite',canonical_symbol='BTCUSDT',selected_exchange=None,direction='long',entry_price='100',leverage=2,take_profits=['101'],stop_loss='99',reference_price='100')
  for source,label in texts.PRICE_SOURCE_LABELS.items():
   data={**base,'price_source_type':source}
   if source!='composite': data.update(mode='single_exchange',selected_exchange=source.removesuffix('_only'))
   rendered=texts.signal_review(data); self.assertIn(label,rendered); self.assertNotIn(source,rendered); self.assertNotIn('_only',rendered)
 async def test_final_persistence_failure_is_sanitized_and_retryable(self):
  state=await self.start(); state.data.update(mode='composite',canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None,native_symbol=None,direction='long',entry_price='100',leverage=2,take_profits=['101'],stop_loss='99',reference_price='100',price_source_type='composite'); state.state=SignalCreate.review.state
  query=await self.cb(state,'confirm',service=PersistFailService())
  self.assertEqual(query.message.answers[-1][0],texts.TEMPORARY_ERROR); self.assertEqual(state.state,SignalCreate.review.state)
 async def test_stale_shared_pagination_failure_after_cancel_is_silent(self):
  state=await self.start(); await signal_routes.shared_query(Message('many'),actor(),state,Catalog()); blocking=BlockingFailCatalog('sp')
  task=asyncio.create_task(self.cb(state,'sp',page=2,catalog=blocking)); await blocking.arrived.wait(); await self.cb(state,'cancel'); blocking.release.set(); query=await task
  self.assertEqual(query.message.answers,[]); self.assertIsNone(state.state)
 async def test_stale_native_pagination_failure_after_start_is_silent(self):
  state=await self.start(); c=Catalog(); await self.cb(state,'out',catalog=c); await self.cb(state,'ex','lbank',catalog=c); await signal_routes.native_query(Message('many'),actor(),state,c); blocking=BlockingFailCatalog('np')
  task=asyncio.create_task(self.cb(state,'np',page=2,catalog=blocking)); await blocking.arrived.wait(); await admin_routes.global_start(Message('/start'),actor(),state); blocking.release.set(); query=await task
  self.assertEqual(query.message.answers,[]); self.assertIsNone(state.state)
 async def test_stale_shared_verification_failure_after_new_search_is_silent(self):
  state=await self.start(); await signal_routes.shared_query(Message('BTC'),actor(),state,Catalog()); blocking=BlockingFailCatalog('ss')
  task=asyncio.create_task(self.cb(state,'ss','0',1,catalog=blocking)); await blocking.arrived.wait(); await signal_routes.shared_query(Message('new'),actor(),state,Catalog()); current=state.data.copy(); blocking.release.set(); query=await task
  self.assertEqual(query.message.answers,[]); self.assertEqual(state.state,SignalCreate.shared_search.state); self.assertEqual(state.data,current)
 async def test_stale_native_verification_failure_after_exchange_change_is_silent(self):
  state=await self.start(); c=Catalog(); await self.cb(state,'out',catalog=c); await self.cb(state,'ex','lbank',catalog=c); await signal_routes.native_query(Message('DOGE'),actor(),state,c); blocking=BlockingFailCatalog('ns')
  task=asyncio.create_task(self.cb(state,'ns','0',1,catalog=blocking)); await blocking.arrived.wait(); await self.cb(state,'back','exchange',catalog=c); await self.cb(state,'ex','ourbit',catalog=c); current=state.data.copy(); blocking.release.set(); query=await task
  self.assertEqual(query.message.answers,[]); self.assertEqual(state.state,SignalCreate.native_search.state); self.assertEqual(state.data,current); self.assertEqual(state.data['selected_exchange'],'ourbit')
 async def test_start_wins_before_finalizing_claim(self):
  state=await self.review(); service=Service(); arrived=asyncio.Event(); release=asyncio.Event(); service.gate=(arrived,release)
  task=asyncio.create_task(self.cb(state,'confirm',service=service)); await arrived.wait(); await admin_routes.global_start(Message('/start'),actor(),state); release.set(); query=await task
  self.assertEqual(service.created,[]); self.assertIsNone(state.state); self.assertEqual(query.message.answers,[])
 async def test_finalizing_claim_blocks_cancel_and_commits_once(self):
  state=await self.review(); service=BlockingPersistenceService(); task=asyncio.create_task(self.cb(state,'confirm',service=service)); await service.persist_arrived.wait()
  self.assertEqual(state.state,SignalCreate.finalizing.state); cancel=await self.cb(state,'cancel',service=service); self.assertEqual(cancel.message.answers[-1][0],texts.SIGNAL_FINALIZING); self.assertNotIn(texts.FLOW_CANCELLED,[x[0] for x in cancel.message.answers])
  service.persist_release.set(); query=await task; self.assertEqual(len(service.created),1); self.assertIsNone(state.state); self.assertEqual(query.message.answers[-1][0],texts.SIGNAL_RECORDED_SUCCESS)
 async def test_competing_confirmations_claim_and_persist_once(self):
  state=await self.review(); service=BlockingPersistenceService(); first=asyncio.create_task(self.cb(state,'confirm',service=service)); await service.persist_arrived.wait(); second=await self.cb(state,'confirm',service=service)
  self.assertEqual(second.message.answers,[]); self.assertEqual(state.state,SignalCreate.finalizing.state); service.persist_release.set(); await first; self.assertEqual(len(service.created),1)
 async def test_persistence_failure_returns_to_review_and_reuses_creation_token(self):
  state=await self.review(); creation=state.data['creation_token']; failing=BlockingPersistenceService(fail=True); task=asyncio.create_task(self.cb(state,'confirm',service=failing)); await failing.persist_arrived.wait(); failing.persist_release.set(); query=await task
  self.assertEqual(state.state,SignalCreate.review.state); self.assertEqual(state.data['creation_token'],creation); self.assertEqual(query.message.answers[-1][0],texts.TEMPORARY_ERROR)
  succeeding=BlockingPersistenceService(); retry=asyncio.create_task(self.cb(state,'confirm',service=succeeding)); await succeeding.persist_arrived.wait(); self.assertEqual(succeeding.created,[]); succeeding.persist_release.set(); await retry
  self.assertEqual(len(succeeding.created),1); self.assertEqual(succeeding.created[0].creation_token,creation); self.assertIsNone(state.state)
 async def test_obsolete_persistence_failure_is_silent(self):
  state=await self.review(); failing=BlockingPersistenceService(fail=True); task=asyncio.create_task(self.cb(state,'confirm',service=failing)); await failing.persist_arrived.wait()
  async with signal_routes.phase5_lock(Message()):
   await state.set_state(SignalCreate.shared_search); await state.set_data({'flow_token':'new-flow','creation_token':'new-token','signalist_id':7})
  current=state.data.copy(); failing.persist_release.set(); query=await task
  self.assertEqual(query.message.answers,[]); self.assertEqual(state.state,SignalCreate.shared_search.state); self.assertEqual(state.data,current)
 async def test_orphan_finalizing_with_signal_clears_and_reports_success(self):
  state=await self.review(); state.state=SignalCreate.finalizing.state; state.data['final_validation_token']='final'; service=Service(); service.stored=SimpleNamespace(id=1); message=Message()
  outcome=await signal_routes.reconcile_finalizing(message,message,state,service)
  self.assertEqual(outcome,'success'); self.assertIsNone(state.state); self.assertEqual(message.answers[-1][0],texts.SIGNAL_RECORDED_SUCCESS)
 async def test_orphan_finalizing_without_signal_restores_complete_review(self):
  state=await self.review(); original=state.data.copy(); state.state=SignalCreate.finalizing.state; state.data['final_validation_token']='obsolete'; message=Message()
  outcome=await signal_routes.reconcile_finalizing(message,message,state,Service())
  self.assertEqual(outcome,'review'); self.assertEqual(state.state,SignalCreate.review.state); self.assertEqual(state.data['creation_token'],original['creation_token']); self.assertNotIn('final_validation_token',state.data); self.assertEqual(state.data['take_profits'],original['take_profits'])
 async def test_live_finalizing_and_temporary_reconciliation_failure(self):
  state=await self.review(); state.state=SignalCreate.finalizing.state; state.data['final_validation_token']='final'; message=Message(); identity=identity_from_message(message); claim=FinalizationClaim(identity,state.data['flow_token'],state.data['creation_token'],'final',state.data['canonical_symbol'])
  phase5_coordination.register_claim(claim)
  try:
   self.assertEqual(await signal_routes.reconcile_finalizing(message,message,state,Service()),'live'); self.assertEqual(message.answers[-1][0],texts.SIGNAL_FINALIZING)
  finally: phase5_coordination.remove_claim(claim)
  service=Service(); service.read_error=SignalCreationError('controlled'); message=Message(); self.assertEqual(await signal_routes.reconcile_finalizing(message,message,state,service),'error'); self.assertEqual(state.state,SignalCreate.finalizing.state); self.assertEqual(message.answers[-1][0],texts.TEMPORARY_ERROR)
 async def test_old_page_shared_and_native_selections_are_discarded(self):
  state=await self.start(); await signal_routes.shared_query(Message('many'),actor(),state,Catalog()); blocking=BlockingFailCatalog('ss'); task=asyncio.create_task(self.cb(state,'ss','0',1,catalog=blocking)); await blocking.arrived.wait(); await self.cb(state,'sp',page=2); blocking.release.set(); query=await task
  self.assertEqual(query.message.answers,[]); self.assertEqual(state.state,SignalCreate.shared_search.state); self.assertEqual(state.data['search_page'],2)
  state=await self.start(); c=Catalog(); await self.cb(state,'out',catalog=c); await self.cb(state,'ex','lbank',catalog=c); await signal_routes.native_query(Message('many'),actor(),state,c); blocking=BlockingFailCatalog('ns'); task=asyncio.create_task(self.cb(state,'ns','0',1,catalog=blocking)); await blocking.arrived.wait(); await self.cb(state,'np',page=2,catalog=c); blocking.release.set(); query=await task
  self.assertEqual(query.message.answers,[]); self.assertEqual(state.state,SignalCreate.native_search.state); self.assertEqual(state.data['search_page'],2)
 async def test_complete_short_wizard(self):
  state=await self.start(); c=Catalog(); service=Service(); await signal_routes.shared_query(Message('BTC'),actor(),state,c); await self.cb(state,'ss','0',1,c,service); await self.cb(state,'dir','short',catalog=c,service=service); await signal_routes.entry_input(Message('100'),actor(),state,service); await signal_routes.leverage_input(Message('3'),actor(),state); await signal_routes.tp_input(Message('99'),actor(),state); await signal_routes.tp_input(Message('98'),actor(),state); await self.cb(state,'tp','finish',catalog=c,service=service); await signal_routes.sl_input(Message('101'),actor(),state); await self.cb(state,'confirm',catalog=c,service=service)
  self.assertEqual(len(service.created),1); self.assertEqual(tuple(str(x) for x in service.created[0].take_profits),('99','98')); self.assertIsNone(state.state)
 async def test_deactivation_during_validation_and_finalizing(self):
  state=await self.review(); service=Service(); arrived=asyncio.Event(); release=asyncio.Event(); service.gate=(arrived,release); task=asyncio.create_task(self.cb(state,'confirm',service=service)); await arrived.wait(); await self.cb(state,'confirm',service=service,actor_value=actor(SignalistStatus.INACTIVE)); release.set(); await task; self.assertEqual(service.created,[]); self.assertIsNone(state.state)
  state=await self.review(); service=BlockingInactiveService(); task=asyncio.create_task(self.cb(state,'confirm',service=service)); await service.persist_arrived.wait(); stale=await self.cb(state,'confirm',service=service,actor_value=actor(SignalistStatus.INACTIVE)); self.assertEqual(state.state,SignalCreate.finalizing.state); self.assertEqual(stale.message.answers[-1][0],texts.SIGNAL_FINALIZING); service.persist_release.set(); query=await task; self.assertIsNone(state.state); self.assertEqual(query.message.answers[-1][0],texts.INACTIVE)
 async def test_signalist_status_is_top_level_across_creation_states(self):
  for value in (SignalCreate.shared_search,SignalCreate.native_search,SignalCreate.entry,SignalCreate.leverage,SignalCreate.take_profits,SignalCreate.stop_loss,SignalCreate.review):
   state=await self.review(); state.state=value.state; message=Message(texts.MENU_SIGNALIST_STATUS); await admin_routes.global_status(message,actor(),state,Service()); self.assertIsNone(state.state); self.assertIn('وضعیت حساب',message.answers[-1][0])

class CallbackSchemaTests(unittest.TestCase):
 def test_ascii_typed_and_bounded(self):
  value=signal_callback('confirm','abcdef1234'); self.assertTrue(value.isascii()); self.assertLessEqual(len(value.encode()),64)
  parsed=SignalCallback.unpack(value); self.assertEqual(parsed.kind,'confirm')

class ErrorService(Service):
 def __init__(self,error): super().__init__(); self.error=error
 async def validate_entry(self,c): raise self.error

class PersistFailService(Service):
 async def create_validated(self,c,r): raise SignalCreationError('database detail')

def _labels(markup):
 return {button.text for row in markup.inline_keyboard for button in row}
