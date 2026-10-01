from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from chainura.application.access import Actor, ActorKind
from chainura.application.reporting import ReportSummary, TradeHistoryPage, period_filter
from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.telegram import report_routes
from chainura.telegram.presentation import texts

class State:
    def __init__(self,data=None):self.data=dict(data or {});self.state=None
    async def get_data(self):return dict(self.data)
    async def update_data(self,**kw):self.data.update(kw)
    async def clear(self):self.data={};self.state=None
    async def set_state(self,value):self.state=value.state if hasattr(value,'state') else value
class Message:
    def __init__(self,text=''):self.text=text;self.answers=[]
    async def answer(self,text,reply_markup=None):self.answers.append((text,reply_markup))
class Query:
    def __init__(self,data):self.data=data;self.message=Message();self.acks=0
    async def answer(self):self.acks+=1

def active_actor(owner=77):
    now=datetime.now(timezone.utc)
    return Actor(ActorKind.ACTIVE_SIGNALIST,Signalist.rehydrate(id=owner,telegram_user_id=70077,
        display_name='owner',telegram_username=None,status=SignalistStatus.ACTIVE,created_at=now,updated_at=now,archived_at=None))

class Service:
    def __init__(self):self.owners=[]
    async def signalist_summary(self,owner,flt):self.owners.append(owner);return ReportSummary()
    async def signalist_history(self,owner,flt,page):self.owners.append(owner);return TradeHistoryPage((),page,1,0)

class Phase7RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_personal_callback_always_uses_actor_owner_not_fsm(self):
        service=Service();state=State({'signalist_id':999})
        query=Query('p7:s:summary:today')
        await report_routes.personal_callback(query,active_actor(77),state,service)
        self.assertEqual(service.owners,[77]);self.assertNotIn('signalist_id',query.data)
        query=Query('p7:s:hist:1')
        await report_routes.personal_callback(query,active_actor(77),state,service)
        self.assertEqual(service.owners,[77,77])
    async def test_malformed_personal_callbacks_are_controlled(self):
        for payload in ('p7:s:hist:x','p7:s:summary:unknown','p7:s:hist:0','p7:s:hist:-1','p7:s:hist','p7:s:hist:1:extra'):
            query=Query(payload);service=Service()
            await report_routes.personal_callback(query,active_actor(),State(),service)
            self.assertEqual(service.owners,[]);self.assertEqual(query.acks,1)
    def test_custom_button_and_exact_tehran_boundaries(self):
        keyboard=report_routes._periods('p7:s:summary')
        self.assertTrue(any(button.text==texts.REPORT_CUSTOM for row in keyboard.inline_keyboard for button in row))
        value=period_filter('custom',datetime(2026,1,1,tzinfo=timezone.utc),start=date(2026,3,21),end=date(2026,3,21))
        self.assertEqual(value.start_utc,datetime(2026,3,20,20,30,tzinfo=timezone.utc))
        self.assertEqual(value.end_utc,datetime(2026,3,21,20,30,tzinfo=timezone.utc))
        self.assertEqual(report_routes._range_labels(value),("2026-03-21","2026-03-21"))
