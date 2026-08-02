import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from chainura.application.access import Actor, ActorKind
from chainura.application.pagination import Page
from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.domain.destination import Destination, DestinationAccessLevel, DestinationStatus, DestinationType, Exchange
from chainura.telegram import admin_routes
from chainura.telegram.presentation import texts
from chainura.telegram.states import (AdminDestinationCreate, AdminDestinationPriority,
                                      AdminSignalistArchive, AdminSignalistCreate)


class FakeState:
    def __init__(self, state=None, data=None): self.state=state; self.data=dict(data or {}); self.clears=0
    async def get_state(self): return self.state
    async def set_state(self,state): self.state=state.state if hasattr(state,"state") else state
    async def get_data(self): return dict(self.data)
    async def update_data(self,**values): self.data.update(values)
    async def set_data(self,values): self.data=dict(values)
    async def clear(self): self.state=None; self.data={}; self.clears+=1

class FakeMessage:
    def __init__(self,text=""):
        self.text=text; self.answers=[]
        self.chat=SimpleNamespace(id=1); self.from_user=SimpleNamespace(id=2)
    async def answer(self,text,reply_markup=None): self.answers.append((text,reply_markup))

class FakeQuery:
    def __init__(self,data): self.data=data; self.message=FakeMessage()

class FakeAck:
    def __init__(self): self.calls=[]
    async def answer_once(self,text=None): self.calls.append(text); return True

def signalist(status=SignalistStatus.ACTIVE):
    now=datetime.now(timezone.utc)
    return Signalist.rehydrate(id=1,telegram_user_id=10,display_name="Name",telegram_username=None,
        status=status,created_at=now,updated_at=now,archived_at=now if status is SignalistStatus.ARCHIVED else None)

class SignalistService:
    def __init__(self,status=SignalistStatus.ACTIVE):
        self.item=signalist(status); self.archive=AsyncMock(); self.activate=AsyncMock(); self.deactivate=AsyncMock()
    async def get_by_id(self,_): return self.item
    async def list_page(self,*_): return Page((),1,8,0)

class AdminService:
    async def assignment_count(self,_): return 0
    create=AsyncMock()

class DestinationService:
    def __init__(self):
        self.replace_priority=AsyncMock()
        self.detail_calls=0
    async def list_page(self,*_): return Page((),1,8,0)
    async def detail(self,_):
        self.detail_calls+=1; now=datetime.now(timezone.utc)
        item=Destination.rehydrate(id=1,internal_name="D",telegram_chat_id=-1,
            destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PRIVATE,
            status=DestinationStatus.INACTIVE,daily_limit=1,created_at=now,updated_at=now)
        return SimpleNamespace(destination=item,priority=SimpleNamespace(exchanges=(Exchange.OURBIT,)),assigned_count=0)
class AssignmentService: pass

class AdminHandlerTests(unittest.IsolatedAsyncioTestCase):
    actor=Actor(ActorKind.MAIN_ADMIN)
    async def invoke(self,data,state,signalists=None):
        return await self.invoke_with_destination(data,state,DestinationService(),signalists)

    async def invoke_with_destination(self,data,state,destinations,signalists=None):
        query=FakeQuery(data); ack=FakeAck(); signalists=signalists or SignalistService()
        await admin_routes.callbacks(query,self.actor,ack,state,signalists,destinations,AssignmentService(),AdminService())
        self.assertEqual(len(ack.calls),1)
        return query,signalists

    async def test_global_start_clears_every_phase4_state(self):
        states=[AdminSignalistCreate.telegram_user_id,AdminSignalistCreate.display_name,
            AdminSignalistCreate.review,
            AdminDestinationCreate.discovering,AdminDestinationCreate.telegram_chat_id,
            AdminDestinationCreate.candidate_review,AdminDestinationCreate.access_level,
            AdminDestinationCreate.daily_limit,AdminDestinationCreate.priority,
            AdminDestinationCreate.review,AdminDestinationPriority.editing,
            AdminDestinationPriority.review,AdminSignalistArchive.confirming]
        for value in states:
            state=FakeState(value.state,{"sentinel":"keep"}); message=FakeMessage("/start")
            await admin_routes.global_start(message,self.actor,state)
            self.assertIsNone(state.state); self.assertEqual(state.data,{})

    async def test_help_does_not_change_form_state(self):
        for value in (AdminSignalistCreate.telegram_user_id,AdminSignalistCreate.display_name,
                      AdminSignalistCreate.review,
                      AdminDestinationCreate.discovering,AdminDestinationCreate.telegram_chat_id,
                      AdminDestinationCreate.candidate_review,AdminDestinationCreate.access_level,
                      AdminDestinationCreate.daily_limit,AdminDestinationCreate.priority,
                      AdminDestinationCreate.review,AdminDestinationPriority.editing,
                      AdminDestinationPriority.review):
            state=FakeState(value.state,{"sentinel":1}); message=FakeMessage("/help")
            await admin_routes.global_help(message,self.actor)
            self.assertEqual(state.state,value.state); self.assertIn(texts.HELP_ADMIN,message.answers[0][0])

    async def test_top_level_navigation_clears_every_phase4_state(self):
        states=(AdminSignalistCreate.telegram_user_id,AdminSignalistCreate.display_name,
                AdminDestinationCreate.discovering,AdminDestinationCreate.priority,
                AdminDestinationPriority.editing,AdminDestinationPriority.review)
        for value in states:
            for handler,service in ((admin_routes.global_main,None),
                                    (admin_routes.signalists_menu,SignalistService()),
                                    (admin_routes.destinations_menu,DestinationService())):
                state=FakeState(value.state,{"sentinel":1}); message=FakeMessage()
                if handler is admin_routes.global_main: await handler(message,self.actor,state)
                else: await handler(message,self.actor,state,service)
                self.assertIsNone(state.state); self.assertEqual(state.data,{})

    async def test_form_filter_rejects_commands_and_navigation(self):
        filter_=admin_routes.FormInput()
        for value in ("/start","/help",texts.MENU_MAIN,texts.MENU_ADMIN_SIGNALISTS,texts.MENU_ADMIN_DESTINATIONS):
            self.assertFalse(await filter_(FakeMessage(value)))
        self.assertTrue(await filter_(FakeMessage("مقدار واقعی")))

    async def test_signalist_archive_is_two_step_and_state_bound(self):
        service=SignalistService(); state=FakeState()
        query,_=await self.invoke("p4:sl:status:1:archive-request",state,service)
        service.archive.assert_not_awaited(); self.assertEqual(state.state,AdminSignalistArchive.confirming.state)
        await self.invoke("p4:sl:status:1:archive-confirm",state,service)
        service.archive.assert_awaited_once_with(1); self.assertIsNone(state.state)

    async def test_cross_entity_archive_confirm_is_expired(self):
        service=SignalistService(); state=FakeState(AdminSignalistArchive.confirming.state,{"signalist_id":2})
        query,_=await self.invoke("p4:sl:status:1:archive-confirm",state,service)
        service.archive.assert_not_awaited(); self.assertEqual(query.message.answers[-1][0],texts.FLOW_EXPIRED)

    async def test_archive_cancel_via_detail_does_not_archive(self):
        service=SignalistService(); state=FakeState(AdminSignalistArchive.confirming.state,{"signalist_id":1})
        await self.invoke("p4:sl:view:1",state,service)
        service.archive.assert_not_awaited(); self.assertIsNone(state.state)

    async def test_creation_callback_requires_exact_state_and_keys(self):
        state=FakeState(AdminDestinationCreate.access_level.state,{})
        query,_=await self.invoke("p4:nav:candidate-confirm:-1001",state)
        self.assertEqual(query.message.answers[-1][0],texts.FLOW_EXPIRED)
        self.assertEqual(state.data,{})

    async def test_back_transitions_are_contextual(self):
        cases=[
            (AdminSignalistCreate.display_name,{"telegram_user_id":1},"p4:nav:back:slc:id",AdminSignalistCreate.telegram_user_id),
            (AdminDestinationCreate.candidate_review,{"flow_token":"f","internal_name":"D","telegram_chat_id":-1001,"destination_type":"channel"},"p4:nav:back:dsc:chat",AdminDestinationCreate.telegram_chat_id),
            (AdminDestinationCreate.daily_limit,{"internal_name":"D"},"p4:nav:back:dsc:access",AdminDestinationCreate.access_level),
        ]
        for initial,data,callback,target in cases:
            state=FakeState(initial.state,data); await self.invoke(callback,state)
            self.assertEqual(state.state,target.state)

    async def test_destination_back_keyboards_allow_forward_progress(self):
        data={"flow_token":"f","internal_name":"D","telegram_chat_id":-1001,"destination_type":"channel"}
        state=FakeState(AdminDestinationCreate.access_level.state,data)
        query,_=await self.invoke("p4:nav:back:dsc:candidate",state)
        self.assertEqual(state.state,AdminDestinationCreate.candidate_review.state)
        labels={item.text for row in query.message.answers[-1][1].inline_keyboard for item in row}
        self.assertIn(texts.BUTTON_CONFIRM_DESTINATION,labels)

    async def test_cancel_clears_creation_flows(self):
        for initial,flow in ((AdminSignalistCreate.display_name,"slc"),(AdminDestinationCreate.daily_limit,"dsc")):
            state=FakeState(initial.state,{"temporary":"value"}); await self.invoke(f"p4:nav:cancel:{flow}",state)
            self.assertIsNone(state.state); self.assertEqual(state.data,{})

    async def test_priority_callback_requires_matching_destination(self):
        now=datetime.now(timezone.utc).isoformat()
        state=FakeState(AdminDestinationPriority.review.state,
            {"destination_id":2,"expected_updated_at":now,"exchanges":["ourbit"]})
        query,_=await self.invoke("p4:dp:confirm:1",state)
        self.assertEqual(query.message.answers[-1][0],texts.FLOW_EXPIRED)

    async def test_priority_review_back_preserves_temporary_order(self):
        now=datetime.now(timezone.utc).isoformat(); destinations=DestinationService()
        data={"destination_id":1,"expected_updated_at":now,"exchanges":["lbank","ourbit"]}
        state=FakeState(AdminDestinationPriority.editing.state,data)
        await self.invoke_with_destination("p4:dp:review:1",state,destinations)
        self.assertEqual(state.state,AdminDestinationPriority.review.state)
        query,_=await self.invoke_with_destination("p4:dp:back:1",state,destinations)
        self.assertEqual(state.state,AdminDestinationPriority.editing.state)
        self.assertEqual(state.data["exchanges"],["lbank","ourbit"])
        self.assertEqual(destinations.detail_calls,0); destinations.replace_priority.assert_not_awaited()
        labels=[item.text for row in query.message.answers[-1][1].inline_keyboard for item in row]
        self.assertLess(labels.index("LBank"),labels.index("Ourbit"))
        await self.invoke_with_destination("p4:dp:review:1",state,destinations)
        await self.invoke_with_destination("p4:dp:confirm:1",state,destinations)
        destinations.replace_priority.assert_awaited_once()

    async def test_priority_review_back_rejects_cross_destination(self):
        now=datetime.now(timezone.utc).isoformat(); destinations=DestinationService()
        state=FakeState(AdminDestinationPriority.review.state,
            {"destination_id":2,"expected_updated_at":now,"exchanges":["ourbit"]})
        query,_=await self.invoke_with_destination("p4:dp:back:1",state,destinations)
        self.assertEqual(query.message.answers[-1][0],texts.FLOW_EXPIRED)
        self.assertEqual(state.state,AdminDestinationPriority.review.state)

    async def test_destination_creation_reaches_review_without_persistence(self):
        state=FakeState(AdminDestinationCreate.priority.state,{"flow_token":"f","internal_name":"D","telegram_chat_id":-1,
            "destination_type":"channel","access_level":"private","daily_limit":1,"exchanges":["ourbit"]})
        query,_=await self.invoke("p4:nav:back:dsc:review",state)
        self.assertEqual(state.state,AdminDestinationCreate.review.state)
        self.assertIn("بررسی نهایی مقصد",query.message.answers[-1][0])

    async def test_non_admin_cannot_open_management_callback(self):
        for kind in (ActorKind.ACTIVE_SIGNALIST,ActorKind.INACTIVE_SIGNALIST,
                     ActorKind.ARCHIVED_SIGNALIST,ActorKind.UNKNOWN):
            ack=FakeAck(); query=FakeQuery("p4:sl:create")
            await admin_routes.callbacks(query,Actor(kind),ack,FakeState(),SignalistService(),DestinationService(),AssignmentService(),AdminService())
            self.assertEqual(len(ack.calls),1)
