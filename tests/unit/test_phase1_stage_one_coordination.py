import asyncio
import unittest
from types import SimpleNamespace

from chainura.application.access import Actor, ActorKind
from chainura.errors import InitialEntryDriftExceededError, SignalCreationError
from chainura.telegram.phase5_coordination import phase5_coordination
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.signal_callbacks import SignalCallback
from chainura.telegram.signal_routes import (leverage_input,
    reconcile_stage_one_creating, signal_callback_handler, start_signal)
from chainura.telegram.states import SignalCreate


class State:
    def __init__(self, state=None, data=None):
        self.state = state.state if hasattr(state, "state") else state
        self.data = dict(data or {})

    async def get_state(self): return self.state
    async def set_state(self, state): self.state = state.state
    async def get_data(self): return dict(self.data)
    async def set_data(self, data): self.data = dict(data)
    async def update_data(self, **data): self.data.update(data)
    async def clear(self): self.state = None; self.data = {}


class Message:
    def __init__(self, text=""):
        self.chat = SimpleNamespace(id=700)
        self.from_user = SimpleNamespace(id=701)
        self.text = text
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class Ack:
    async def answer_once(self): pass


class BlockingService:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.create_calls = 0
        self.get_calls = 0
        self.existing = None
        self.failure = None

    async def create_pending(self, command):
        self.create_calls += 1
        self.entered.set()
        await self.release.wait()
        if self.failure:
            raise self.failure
        self.existing = SimpleNamespace(id=42)
        return self.existing

    async def get_by_creation_token(self, creation_token, signalist_id):
        self.get_calls += 1
        if self.failure and isinstance(self.failure, SignalCreationError):
            raise self.failure
        return self.existing


def draft():
    return {"flow_token":"flow","creation_token":"creation","signalist_id":9,
        "mode":"composite","canonical_symbol":"BTCUSDT","base_asset":"BTC",
        "quote_asset":"USDT","selected_exchange":None,"native_symbol":None,
        "direction":"long","leverage":10,"p0_reference_price":"100",
        "p0_source_prices":[("ourbit","100"),("toobit","100")],
        "p0_native_symbols":[("ourbit","BTCUSDT"),("toobit","BTCUSDT"),
                             ("lbank","BTCUSDT")],
        "p0_price_source_type":"composite"}


class StageOneCoordinationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor=Actor(ActorKind.ACTIVE_SIGNALIST,SimpleNamespace(id=9))
        self.message=Message()
        self.callback=SimpleNamespace(message=self.message,from_user=self.message.from_user)
        self.service=BlockingService()

    async def route(self, kind):
        await signal_callback_handler(self.callback,
            SignalCallback(kind=kind,flow="flow",ref="-",page=0),self.actor,
            self.state,Ack(),None,self.service)

    async def test_leverage_only_enters_review_without_creation(self):
        self.state=State(SignalCreate.leverage,draft())
        await leverage_input(Message("50"),self.actor,self.state,self.service)
        self.assertEqual(await self.state.get_state(),SignalCreate.stage_one_review.state)
        self.assertEqual(self.service.create_calls,0)
        self.assertEqual((await self.state.get_data())["p0_reference_price"],"100")

    async def test_live_claim_blocks_double_confirm_cancel_and_new_flow(self):
        self.state=State(SignalCreate.stage_one_review,draft())
        creating=asyncio.create_task(self.route("confirm1"))
        await self.service.entered.wait()
        self.assertEqual(await self.state.get_state(),SignalCreate.stage_one_creating.state)

        await self.route("confirm1")
        await self.route("cancel")
        await start_signal(self.message,self.actor,self.state,self.service)

        self.assertEqual(self.service.create_calls,1)
        self.assertEqual(await self.state.get_state(),SignalCreate.stage_one_creating.state)
        self.assertFalse(any(text==texts.FLOW_CANCELLED for text,_ in self.message.answers))
        self.assertGreaterEqual(sum(text==texts.SIGNAL_STAGE_ONE_CREATING
                                    for text,_ in self.message.answers),3)

        self.service.release.set()
        await creating
        self.assertIsNone(await self.state.get_state())
        self.assertTrue(any("TR-000042" in text for text,_ in self.message.answers))
        self.assertEqual(phase5_coordination.claim_count_for_tests(),0)

    async def test_orphan_with_durable_signal_recovers_without_create(self):
        data=draft();data["stage_one_token"]="operation"
        self.state=State(SignalCreate.stage_one_creating,data)
        self.service.existing=SimpleNamespace(id=42)
        outcome=await reconcile_stage_one_creating(self.message,self.message,self.state,
            self.service,actor=self.actor)
        self.assertEqual(outcome,"success")
        self.assertEqual(self.service.create_calls,0)
        self.assertEqual(self.service.get_calls,1)
        self.assertIsNone(await self.state.get_state())
        self.assertIn("TR-000042",self.message.answers[-1][0])

    async def test_orphan_without_signal_restores_exact_review_draft(self):
        data=draft();data["stage_one_token"]="operation"
        self.state=State(SignalCreate.stage_one_creating,data)
        outcome=await reconcile_stage_one_creating(self.message,self.message,self.state,
            self.service,actor=self.actor)
        self.assertEqual(outcome,"review")
        restored=await self.state.get_data()
        self.assertNotIn("stage_one_token",restored)
        for key in ("p0_reference_price","canonical_symbol","direction","leverage"):
            self.assertEqual(restored[key],data[key])
        self.assertEqual(await self.state.get_state(),SignalCreate.stage_one_review.state)
        self.assertEqual(self.service.create_calls,0)

    async def test_inactive_orphan_is_cleared_without_creation(self):
        data=draft();data["stage_one_token"]="operation"
        self.state=State(SignalCreate.stage_one_creating,data)
        inactive=Actor(ActorKind.INACTIVE_SIGNALIST,SimpleNamespace(id=9))
        outcome=await reconcile_stage_one_creating(self.message,self.message,self.state,
            self.service,actor=inactive)
        self.assertEqual(outcome,"denied")
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(self.service.create_calls,0)

    async def test_temporary_recovery_error_preserves_creating_state(self):
        data=draft();data["stage_one_token"]="operation"
        self.state=State(SignalCreate.stage_one_creating,data)
        self.service.failure=SignalCreationError("storage unavailable")
        outcome=await reconcile_stage_one_creating(self.message,self.message,self.state,
            self.service,actor=self.actor)
        self.assertEqual(outcome,"error")
        self.assertEqual(await self.state.get_state(),SignalCreate.stage_one_creating.state)
        self.assertEqual(await self.state.get_data(),data)

    async def test_preconfirm_cancel_and_drift_create_no_success(self):
        self.state=State(SignalCreate.stage_one_review,draft())
        await self.route("cancel")
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(self.service.create_calls,0)

        self.state=State(SignalCreate.stage_one_review,draft())
        self.service.failure=InitialEntryDriftExceededError("drift")
        creating=asyncio.create_task(self.route("confirm1"))
        await self.service.entered.wait();self.service.release.set();await creating
        self.assertIsNone(await self.state.get_state())
        self.assertEqual(self.service.create_calls,1)
        self.assertTrue(any(text==texts.SIGNAL_DRIFT_EXCEEDED
                            for text,_ in self.message.answers))


if __name__ == "__main__":
    unittest.main()
