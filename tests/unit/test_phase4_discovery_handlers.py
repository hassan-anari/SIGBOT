import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from chainura.application.access import Actor, ActorKind
from chainura.domain.destination import DestinationType
from chainura.errors import (ApplicationError, DuplicateDestinationChatIdError,
    DuplicateDestinationInternalNameError)
from chainura.telegram import admin_routes
from chainura.telegram.destination_inspector import (DestinationInspection,
    DestinationInspectionError, InspectionError)
from chainura.telegram.presentation import texts
from chainura.telegram.states import AdminDestinationCreate, AdminDestinationEdit
from tests.unit.test_phase4_admin_handlers import (AdminService, AssignmentService,
    DestinationService, FakeAck, FakeMessage, FakeQuery, FakeState, SignalistService)


ACTOR = Actor(ActorKind.MAIN_ADMIN)


class Inspector:
    def __init__(self, result=None, error=None):
        self.result=result or DestinationInspection(-100123,"مقصد",DestinationType.CHANNEL,True)
        self.error=error; self.calls=[]
    async def inspect(self, chat_id):
        self.calls.append(chat_id)
        if self.error: raise DestinationInspectionError(self.error)
        return self.result


class BlockingInspector(Inspector):
    def __init__(self, result=None):
        super().__init__(result); self.started=asyncio.Event(); self.release=asyncio.Event()
    async def inspect(self, chat_id):
        self.calls.append(chat_id); self.started.set(); await self.release.wait(); return self.result


class RecordingDestinationService(DestinationService):
    def __init__(self):
        super().__init__(); self.create=AsyncMock(); self.update_configuration=AsyncMock()


async def invoke(data,state,destinations=None,inspector=None):
    query=FakeQuery(data); ack=FakeAck()
    await admin_routes.callbacks(query,ACTOR,ack,state,SignalistService(),
        destinations or RecordingDestinationService(),AssignmentService(),AdminService(),inspector)
    return query,ack


def creation_data(**changes):
    data={"flow_token":"flow","internal_name":"مقصد","telegram_chat_id":-100123,
          "destination_type":"channel","access_level":"private","daily_limit":5,
          "exchanges":["lbank","ourbit"]}
    data.update(changes); return data


class DiscoveryHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_normalizes_all_supported_chat_forms(self):
        cases=(("123",-100123,DestinationType.CHANNEL),
               ("-123456789",-123456789,DestinationType.GROUP),
               ("100123",-100123,DestinationType.GROUP))
        for raw,expected,dtype in cases:
            state=FakeState(AdminDestinationCreate.telegram_chat_id.state,{"flow_token":"f"})
            inspector=Inspector(DestinationInspection(expected,"<مقصد>",dtype,True))
            message=FakeMessage(raw)
            await admin_routes.ds_chat(message,ACTOR,state,inspector)
            self.assertEqual(state.state,AdminDestinationCreate.candidate_review.state)
            self.assertEqual(state.data["telegram_chat_id"],expected)
            self.assertIn("&lt;مقصد&gt;",message.answers[-1][0])

    async def test_discovery_errors_and_non_admin_remain_at_chat_id(self):
        for error,expected in ((InspectionError.PRIVATE,texts.DESTINATION_PRIVATE),
                               (InspectionError.NOT_FOUND,texts.DESTINATION_NOT_FOUND),
                               (InspectionError.TEMPORARY,texts.TELEGRAM_TEMPORARY)):
            state=FakeState(AdminDestinationCreate.telegram_chat_id.state,{"flow_token":"f"})
            message=FakeMessage("123")
            await admin_routes.ds_chat(message,ACTOR,state,Inspector(error=error))
            self.assertEqual(state.state,AdminDestinationCreate.telegram_chat_id.state)
            self.assertEqual(message.answers[-1][0],expected)
        state=FakeState(AdminDestinationCreate.telegram_chat_id.state,{"flow_token":"f"})
        message=FakeMessage("123"); inspection=DestinationInspection(-100123,"D",DestinationType.CHANNEL,False)
        await admin_routes.ds_chat(message,ACTOR,state,Inspector(inspection))
        self.assertEqual(message.answers[-1][0],texts.DESTINATION_NOT_ADMIN)

    async def test_candidate_confirm_retry_and_cross_flow_are_state_bound(self):
        state=FakeState(AdminDestinationCreate.candidate_review.state,creation_data())
        await invoke("p4:nav:candidate-confirm:-100123",state)
        self.assertEqual(state.state,AdminDestinationCreate.access_level.state)
        await invoke("p4:nav:access:private",state)
        self.assertEqual(state.state,AdminDestinationCreate.daily_limit.state)
        state=FakeState(AdminDestinationCreate.candidate_review.state,creation_data())
        await invoke("p4:nav:candidate-retry:-100123",state)
        self.assertEqual(state.data,{"flow_token":"flow"})
        self.assertEqual(state.state,AdminDestinationCreate.telegram_chat_id.state)
        query,_=await invoke("p4:nav:access:private",
            FakeState(AdminDestinationEdit.access_level.state,{"destination_id":1,"expected_updated_at":"x"}))
        self.assertEqual(query.message.answers[-1][0],texts.FLOW_EXPIRED)

    async def test_initial_inspection_cannot_resurrect_start_cancel_or_navigation(self):
        for interrupter in ("start","cancel","main","destinations"):
            state=FakeState(AdminDestinationCreate.telegram_chat_id.state,{"flow_token":"f"})
            inspector=BlockingInspector(); message=FakeMessage("123")
            task=asyncio.create_task(admin_routes.ds_chat(message,ACTOR,state,inspector))
            await inspector.started.wait()
            if interrupter == "start": await admin_routes.global_start(FakeMessage(),ACTOR,state)
            elif interrupter == "main": await admin_routes.global_main(FakeMessage(),ACTOR,state)
            elif interrupter == "destinations": await admin_routes.destinations_menu(FakeMessage(),ACTOR,state,DestinationService())
            else: await invoke("p4:nav:cancel:dsc",state)
            inspector.release.set(); await task
            self.assertIsNone(state.state,interrupter); self.assertEqual(state.data,{})
            self.assertFalse(any(texts.CANDIDATE_CHANGED in answer[0] or "مقصد پیدا شد" in answer[0]
                                 for answer in message.answers))

    async def test_final_revalidation_cannot_persist_after_start_or_cancel(self):
        for interrupter in ("start","cancel"):
            state=FakeState(AdminDestinationCreate.review.state,creation_data())
            inspector=BlockingInspector(); destinations=RecordingDestinationService()
            task=asyncio.create_task(invoke("p4:nav:confirm:dsc",state,destinations,inspector))
            await inspector.started.wait()
            if interrupter == "start": await admin_routes.global_start(FakeMessage(),ACTOR,state)
            else: await invoke("p4:nav:cancel:dsc",state,destinations)
            inspector.release.set(); await task
            destinations.create.assert_not_awaited(); self.assertIsNone(state.state)

    async def test_final_revalidation_errors_never_attempt_persistence(self):
        cases = (
            (Inspector(error=InspectionError.NOT_FOUND), texts.DESTINATION_NOT_FOUND),
            (Inspector(error=InspectionError.TEMPORARY), texts.TELEGRAM_TEMPORARY),
            (Inspector(DestinationInspection(-100123, "مقصد", DestinationType.CHANNEL, False)),
             texts.DESTINATION_NOT_ADMIN),
        )
        for inspector, expected in cases:
            with self.subTest(expected=expected):
                state = FakeState(AdminDestinationCreate.review.state, creation_data())
                destinations = RecordingDestinationService()
                query, _ = await invoke("p4:nav:confirm:dsc", state, destinations, inspector)
                destinations.create.assert_not_awaited()
                self.assertEqual(query.message.answers[-1][0], expected)

    async def test_two_final_confirmations_make_at_most_one_persistence_attempt(self):
        state=FakeState(AdminDestinationCreate.review.state,creation_data())
        first=BlockingInspector(); second=BlockingInspector(); destinations=RecordingDestinationService()
        task1=asyncio.create_task(invoke("p4:nav:confirm:dsc",state,destinations,first)); await first.started.wait()
        task2=asyncio.create_task(invoke("p4:nav:confirm:dsc",state,destinations,second)); await second.started.wait()
        first.release.set(); await task1; second.release.set(); await task2
        destinations.create.assert_awaited_once()

    async def test_changed_candidate_is_refreshed_and_unconfirmed_fields_removed(self):
        state=FakeState(AdminDestinationCreate.review.state,creation_data())
        fresh=DestinationInspection(-100123,"نام تازه",DestinationType.GROUP,True)
        destinations=RecordingDestinationService(); query,_=await invoke(
            "p4:nav:confirm:dsc",state,destinations,Inspector(fresh))
        destinations.create.assert_not_awaited()
        self.assertEqual(state.state,AdminDestinationCreate.candidate_review.state)
        self.assertEqual(state.data,{"flow_token":"flow","telegram_chat_id":-100123,
                                    "internal_name":"نام تازه","destination_type":"group"})
        self.assertEqual(query.message.answers[-2][0],texts.CANDIDATE_CHANGED)

    async def test_duplicate_chat_and_discovered_title_are_controlled(self):
        for error,expected in ((DuplicateDestinationChatIdError("duplicate"),texts.DUPLICATE_DESTINATION_CHAT),
                               (DuplicateDestinationInternalNameError("duplicate"),texts.DUPLICATE_DESTINATION_NAME)):
            state=FakeState(AdminDestinationCreate.review.state,creation_data())
            destinations=RecordingDestinationService(); destinations.create.side_effect=error
            query,_=await invoke("p4:nav:confirm:dsc",state,destinations,Inspector())
            self.assertEqual(query.message.answers[-1][0],expected)
            self.assertIsNone(state.state)


class DestinationEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_inline_access_public_private_and_stale_callbacks(self):
        for access in ("public","private"):
            state=FakeState(AdminDestinationEdit.access_level.state,
                {"destination_id":7,"expected_updated_at":datetime.now(timezone.utc).isoformat()})
            await invoke(f"p4:de:access:7:{access}",state)
            self.assertEqual(state.data["access_level"],access)
            self.assertEqual(state.state,AdminDestinationEdit.daily_limit.state)
        query,_=await invoke("p4:de:access:8:public",FakeState(
            AdminDestinationEdit.access_level.state,{"destination_id":7,"expected_updated_at":"x"}))
        self.assertEqual(query.message.answers[-1][0],texts.FLOW_EXPIRED)

    async def test_review_persistence_receives_inline_selection(self):
        stamp=datetime.now(timezone.utc).isoformat(); destinations=RecordingDestinationService()
        state=FakeState(AdminDestinationEdit.access_level.state,
            {"destination_id":7,"expected_updated_at":stamp})
        await invoke("p4:de:access:7:private",state,destinations)
        await admin_routes.edit_limit(FakeMessage("5"),ACTOR,state)
        await invoke("p4:nav:confirm:dse",state,destinations)
        destinations.update_configuration.assert_awaited_once()
        self.assertEqual(destinations.update_configuration.await_args.args[1].value,"private")


class PlainButtonLabelTests(unittest.TestCase):
    def test_buttons_are_plain_while_message_rows_are_escaped(self):
        for name in ("<مقصد>","A & B",'نام "ویژه"',"رضا"):
            item=SimpleNamespace(internal_name=name,display_name=name,
                status=SimpleNamespace(value="active"))
            self.assertIn(name,texts.destination_button_label(item))
            self.assertIn(name,texts.signalist_button_label(item))
            if any(char in name for char in '<&"'):
                self.assertNotIn(name,texts.destination_line(item))


class StaleActionCatalogTests(unittest.IsolatedAsyncioTestCase):
    def test_catalog_owns_controlled_stale_copy(self):
        self.assertEqual(texts.STALE_ACTION,
                         "اطلاعات تغییر کرده است؛ نمای فعلی تازه‌سازی شد.")

    async def test_destination_create_and_edit_application_errors_are_controlled(self):
        state=FakeState(AdminDestinationCreate.review.state,creation_data())
        destinations=RecordingDestinationService()
        destinations.create.side_effect=ApplicationError("technical")
        query,_=await invoke("p4:nav:confirm:dsc",state,destinations,Inspector())
        self.assertEqual(query.message.answers[-1][0],texts.STALE_ACTION)

        stamp=datetime.now(timezone.utc).isoformat()
        state=FakeState(AdminDestinationEdit.review.state,
            {"destination_id":7,"expected_updated_at":stamp,"access_level":"private","daily_limit":5})
        destinations=RecordingDestinationService()
        destinations.update_configuration.side_effect=ApplicationError("technical")
        query,_=await invoke("p4:nav:confirm:dse",state,destinations)
        self.assertEqual(query.message.answers[0][0],texts.STALE_ACTION)

    async def test_priority_assignment_and_status_errors_are_controlled(self):
        stamp=datetime.now(timezone.utc).isoformat()
        destinations=RecordingDestinationService()
        destinations.replace_priority.side_effect=ApplicationError("technical")
        state=FakeState("AdminDestinationPriority:review",
            {"destination_id":1,"expected_updated_at":stamp,"exchanges":["ourbit"]})
        query,_=await invoke("p4:dp:confirm:1",state,destinations)
        self.assertEqual(query.message.answers[0][0],texts.STALE_ACTION)

        assignment=AssignmentService(); assignment.assign=AsyncMock(side_effect=ApplicationError("technical"))
        query=FakeQuery("p4:as:add:1:2"); ack=FakeAck()
        await admin_routes.callbacks(query,ACTOR,ack,FakeState(),SignalistService(),
            RecordingDestinationService(),assignment,AdminService(),None)
        self.assertEqual(query.message.answers[-1][0],texts.STALE_ACTION)

        signalists=SignalistService(); signalists.deactivate.side_effect=ApplicationError("technical")
        query=FakeQuery("p4:sl:status:1:deactivate"); ack=FakeAck()
        await admin_routes.callbacks(query,ACTOR,ack,FakeState(),signalists,
            RecordingDestinationService(),AssignmentService(),AdminService(),None)
        self.assertEqual(query.message.answers[-1][0],texts.STALE_ACTION)
