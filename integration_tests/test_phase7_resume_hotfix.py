from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy import text

from chainura.application.access import Actor, ActorKind
from chainura.application.signal_service import CreatePendingSignalCommand, SignalService
from chainura.domain.signal import SignalLifecycleStatus, SignalMode, TradeDirection
from chainura.persistence.database import create_session_factory
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.domain.signalist import Signalist
from chainura.telegram.active_signal_routes import active_callback
from chainura.telegram.presentation.signal_callbacks import SignalCallback
from chainura.telegram.signal_routes import signal_callback_handler, sl_input, tp_input
from chainura.telegram.states import SignalCreate
from integration_tests.support import create_safe_engine, migrate, require_safe_test_database
from integration_tests.test_phase7_1_staged_signal import SequencePrices, snap


class State:
    def __init__(self): self.state=None;self.data={}
    async def get_state(self): return self.state
    async def set_state(self,state): self.state=state.state
    async def get_data(self): return dict(self.data)
    async def set_data(self,data): self.data=dict(data)
    async def update_data(self,**data): self.data.update(data)
    async def clear(self): self.state=None;self.data={}


class AnswerMessage:
    def __init__(self,user_id):
        self.chat=SimpleNamespace(id=user_id);self.from_user=SimpleNamespace(id=user_id)
        self.text="";self.answers=[]
    async def answer(self,text,reply_markup=None):
        self.answers.append((text,reply_markup))


class Ack:
    async def answer_once(self): pass


class ResumeDetailsHotfixTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls): cls.safe=require_safe_test_database();migrate("head",cls.safe)
    @classmethod
    def tearDownClass(cls): cls.safe.cleanup()

    async def asyncSetUp(self):
        self.engine=create_safe_engine(self.safe);self.sessions=create_session_factory(self.engine)
        async with self.engine.begin() as connection:
            await connection.execute(text("DELETE FROM signals"));await connection.execute(text("DELETE FROM signalists"))
        async with self.sessions() as session,session.begin():
            self.signalist=await SqlAlchemySignalistRepository(session).add(Signalist.create(78901,"Signalist"))
        self.factory=SqlAlchemySignalistTransactionFactory(self.sessions)
        self.service=SignalService(self.factory,SequencePrices(snap("100.5")),lambda:datetime(2026,8,10,tzinfo=timezone.utc))
        self.pending=await self.service.create_pending(CreatePendingSignalCommand(
            self.signalist.id,"resume-hotfix",SignalMode.COMPOSITE,"BTCUSDT","BTC","USDT",
            None,None,TradeDirection.LONG,10,snap("100")))
        self.actor=Actor(ActorKind.ACTIVE_SIGNALIST,self.signalist);self.state=State()
        self.message=AnswerMessage(self.signalist.telegram_user_id)
        self.callback=SimpleNamespace(message=self.message,from_user=self.message.from_user)

    async def asyncTearDown(self): await self.engine.dispose()

    async def callback_route(self,kind,flow="resume",ref="-"):
        await signal_callback_handler(self.callback,SignalCallback(kind=kind,flow=flow,ref=ref,page=1),
            self.actor,self.state,Ack(),None,self.service)

    async def input(self,handler,value):
        message=AnswerMessage(self.signalist.telegram_user_id);message.text=value
        await handler(message,self.actor,self.state)
        return message

    async def test_durable_resume_review_navigation_and_activation(self):
        await self.callback_route("continue",ref=str(self.pending.id))
        data=await self.state.get_data();flow=data["flow_token"]
        self.assertEqual({key:data[key] for key in ("pending_signal_id","signalist_id","canonical_symbol","direction","entry_price","leverage","take_profits")},
            {"pending_signal_id":self.pending.id,"signalist_id":self.signalist.id,"canonical_symbol":"BTCUSDT","direction":"long","entry_price":"100","leverage":10,"take_profits":[]})
        callbacks=[button.callback_data for row in self.message.answers[-1][1].inline_keyboard for button in row]
        self.assertFalse(any("back" in value and "leverage" in value for value in callbacks))
        await self.callback_route("back",flow,"leverage")
        self.assertEqual(await self.state.get_state(),SignalCreate.take_profits.state)

        tp_message=await self.input(tp_input,"101")
        self.assertFalse(any("leverage" in button.callback_data for row in tp_message.answers[-1][1].inline_keyboard for button in row))
        await self.callback_route("tp",flow,"finish")
        sl_message=await self.input(sl_input,"99")
        review=sl_message.answers[-1][0]
        for expected in ("BTCUSDT","100","لانگ","10","101","99"):
            self.assertIn(expected,review)

        await self.callback_route("back",flow,"sl")
        await self.callback_route("back",flow,"tp")
        self.assertEqual(await self.state.get_state(),SignalCreate.take_profits.state)
        callbacks=[button.callback_data for row in self.message.answers[-1][1].inline_keyboard for button in row]
        self.assertFalse(any("leverage" in value for value in callbacks))
        await self.callback_route("tp",flow,"finish")
        await self.input(sl_input,"99")
        await self.callback_route("confirm",flow)
        active=await self.service.get_by_creation_token("resume-hotfix",self.signalist.id)
        self.assertIs(active.status,SignalLifecycleStatus.ACTIVE)

    async def test_active_signals_continue_delegates_to_durable_route(self):
        message=Message(message_id=1,date=datetime.now(timezone.utc),chat=Chat(id=78901,type="private"),
            from_user=User(id=78901,is_bot=False,first_name="S"),text="x")
        callback=CallbackQuery(id="1",from_user=message.from_user,chat_instance="x",message=message,
            data=f"as7:r:{self.pending.id}")
        item=SimpleNamespace(id=self.pending.id,status=SignalLifecycleStatus.PENDING_DETAILS)
        active_service=SimpleNamespace(get_open=AsyncMock(return_value=item))
        with patch("chainura.telegram.signal_routes.signal_callback_handler",new=AsyncMock()) as delegated:
            await active_callback(callback,self.actor,self.state,Ack(),active_service,self.service)
        delegated.assert_awaited_once()
        self.assertEqual(delegated.await_args.args[1].kind,"continue")


if __name__ == "__main__": unittest.main()
