from __future__ import annotations
import unittest
from datetime import datetime, timezone
from sqlalchemy import text
from chainura.application.publication import UpdatePublicationService
from chainura.persistence.database import create_session_factory
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from integration_tests.support import create_safe_engine,migrate,require_safe_test_database
NOW=datetime(2026,8,4,12,tzinfo=timezone.utc)
class UpdateRecoveryTests(unittest.IsolatedAsyncioTestCase):
 @classmethod
 def setUpClass(cls): cls.safe=require_safe_test_database(); migrate('head',cls.safe)
 @classmethod
 def tearDownClass(cls): cls.safe.cleanup()
 async def asyncSetUp(self):
  self.engine=create_safe_engine(self.safe); self.sessions=create_session_factory(self.engine)
  async with self.engine.begin() as c:
   for table in ('destination_update_publications','update_publication_requests','signal_update_candidates','signal_event_source_prices','signal_events','destination_publications','initial_publication_requests','signal_initial_prices','signal_exchange_symbols','signal_take_profits','signals','destination_signalists','destination_exchange_priorities','destinations','signalists'):
    await c.execute(text(f'DELETE FROM {table}'))
 async def asyncTearDown(self): await self.engine.dispose()
 async def seed(self,statuses,*,request_status='planned',initial_status=None,with_request=True):
  async with self.engine.begin() as c:
   sid=(await c.execute(text("insert into signalists(telegram_user_id,display_name,status,created_at,updated_at) values(900,'S','active',:n,:n) returning id"),{'n':NOW})).scalar_one()
   sig=(await c.execute(text("insert into signals(signalist_id,creation_token,mode,canonical_symbol,base_asset,quote_asset,direction,entry_price,leverage,stop_loss,reference_price,price_source_type,status,created_at,updated_at) values(:sid,'t','composite','BTCUSDT','BTC','USDT','long','100',5,'90','100','composite','active',:n,:n) returning id"),{'sid':sid,'n':NOW})).scalar_one()
   await c.execute(text("insert into signal_exchange_symbols(signal_id,exchange,native_symbol) values(:s,'ourbit','BTCUSDT')"),{'s':sig})
   event=(await c.execute(text("insert into signal_events(signal_id,kind,tp_index,threshold_price,observed_reference_price,roi_basis_price,leveraged_roi,price_source_type,occurred_at,created_at) values(:s,'tp_hit',1,'110','110','110','50','composite',:n,:n) returning id"),{'s':sig,'n':NOW})).scalar_one()
   await c.execute(text("insert into signal_event_source_prices(event_id,exchange,price) values(:e,'ourbit','110')"),{'e':event})
   cand=(await c.execute(text("insert into signal_update_candidates(signal_id,event_id,signalist_id,status,approved_at,created_at,updated_at) values(:s,:e,:sid,'approved',:n,:n,:n) returning id"),{'s':sig,'e':event,'sid':sid,'n':NOW})).scalar_one()
   if initial_status is not None:
    await c.execute(text("insert into initial_publication_requests(signal_id,status,created_at,updated_at) values(:s,:st,:n,:n)"),{'s':sig,'st':initial_status,'n':NOW})
   req=None
   if with_request:
    req=(await c.execute(text("insert into update_publication_requests(candidate_id,signal_id,event_id,status,created_at,updated_at,planned_at) values(:c,:s,:e,:st,:n,:n,case when :st in ('planned','completed','superseded') then :n else null end) returning id"),{'c':cand,'s':sig,'e':event,'st':request_status,'n':NOW})).scalar_one()
   for idx,status in enumerate(statuses,1):
    did=(await c.execute(text("insert into destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit,created_at,updated_at) values(:name,:chat,'channel','private','active',1,:n,:n) returning id"),{'name':f'D{idx}','chat':-idx,'n':NOW})).scalar_one()
    values={'r':req,'c':cand,'s':sig,'e':event,'d':did,'status':status,'n':NOW,'msg':1000+idx,'reply':500+idx}
    if status=='sent':
     await c.execute(text("insert into destination_update_publications(request_id,candidate_id,signal_id,event_id,destination_id,selected_exchange,event_kind,status,telegram_message_id,reply_to_message_id,attempt_count,sent_at,created_at,updated_at) values(:r,:c,:s,:e,:d,'ourbit','tp_hit','sent',:msg,:reply,1,:n,:n,:n)"),values)
    else:
     await c.execute(text("insert into destination_update_publications(request_id,candidate_id,signal_id,event_id,destination_id,selected_exchange,event_kind,status,attempt_count,created_at,updated_at) values(:r,:c,:s,:e,:d,'ourbit','tp_hit',:status,1,:n,:n)"),values)
  return req, cand
 async def recover(self):
  await UpdatePublicationService(SqlAlchemySignalistTransactionFactory(self.sessions),None,None,lambda:NOW).recover()
 async def request_status(self,req):
  async with self.engine.connect() as c:return await c.scalar(text('select status from update_publication_requests where id=:r'),{'r':req})
 async def delivery_statuses(self,req):
  async with self.engine.connect() as c:return tuple((await c.execute(text('select status from destination_update_publications where request_id=:r order by id'),{'r':req})).scalars())

 async def request_count_for_candidate(self,cand):
  async with self.engine.connect() as c:return await c.scalar(text('select count(*) from update_publication_requests where candidate_id=:c'),{'c':cand})
 async def test_pending_zero_delivery_survives_recovery_then_plans(self):
  req,_=await self.seed((),request_status='pending');await self.recover()
  self.assertEqual(await self.request_status(req),'pending')
  async with self.sessions() as s,s.begin():await __import__('chainura.persistence.update_publication_repository',fromlist=['SqlAlchemyUpdatePublicationRepository']).SqlAlchemyUpdatePublicationRepository(s).plan_pending(NOW)
  self.assertEqual(await self.request_status(req),'completed')
 async def test_waiting_initial_zero_delivery_survives_recovery_then_revisited(self):
  req,_=await self.seed((),request_status='waiting_initial',initial_status='pending');await self.recover()
  self.assertEqual(await self.request_status(req),'waiting_initial')
  async with self.engine.begin() as c:await c.execute(text("update initial_publication_requests set status='completed',completed_at=:n,updated_at=:n"),{'n':NOW})
  async with self.sessions() as s,s.begin():await __import__('chainura.persistence.update_publication_repository',fromlist=['SqlAlchemyUpdatePublicationRepository']).SqlAlchemyUpdatePublicationRepository(s).plan_pending(NOW)
  self.assertEqual(await self.request_status(req),'completed')
 async def test_reconciled_missing_request_remains_pending_until_planning(self):
  _,cand=await self.seed((),with_request=False);await self.recover()
  self.assertEqual(await self.request_count_for_candidate(cand),1)
  async with self.engine.connect() as c:req=await c.scalar(text('select id from update_publication_requests where candidate_id=:c'),{'c':cand})
  self.assertEqual(await self.request_status(req),'pending')
  async with self.sessions() as s,s.begin():await __import__('chainura.persistence.update_publication_repository',fromlist=['SqlAlchemyUpdatePublicationRepository']).SqlAlchemyUpdatePublicationRepository(s).plan_pending(NOW)
  self.assertEqual(await self.request_status(req),'completed')

 async def test_sending_becomes_unknown_and_request_completed(self):
  req,_=await self.seed(('sending',));await self.recover()
  self.assertEqual(await self.delivery_statuses(req),('delivery_unknown',));self.assertEqual(await self.request_status(req),'completed')
 async def test_sent_plus_sending_completes(self):
  req,_=await self.seed(('sent','sending'));await self.recover()
  self.assertEqual(await self.delivery_statuses(req),('sent','delivery_unknown'));self.assertEqual(await self.request_status(req),'completed')
 async def test_blocked_plus_sending_remains_incomplete(self):
  req,_=await self.seed(('blocked_template','sending'));await self.recover()
  self.assertEqual(await self.delivery_statuses(req),('blocked_template','delivery_unknown'));self.assertEqual(await self.request_status(req),'planned')
 async def test_all_superseded_becomes_superseded_and_recovery_idempotent(self):
  req,_=await self.seed(('superseded','superseded'));await self.recover();first=await self.delivery_statuses(req)
  self.assertEqual(await self.request_status(req),'superseded')
  await self.recover();self.assertEqual(await self.delivery_statuses(req),first);self.assertEqual(await self.request_status(req),'superseded')
