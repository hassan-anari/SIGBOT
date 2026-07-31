import unittest
from unittest.mock import AsyncMock
from redis.asyncio import Redis
from aiogram.dispatcher.middlewares.error import ErrorsMiddleware
from aiogram.dispatcher.middlewares.user_context import UserContextMiddleware
from aiogram.fsm.middleware import FSMContextMiddleware
from aiogram.fsm.storage.memory import MemoryStorage, DisabledEventIsolation
from chainura.telegram.middlewares import (TelegramErrorBoundary,PrivateChatGate,
    IdempotencyMiddleware,ActorResolutionMiddleware,CallbackAcknowledger)
from chainura.telegram.runtime import build_dispatcher, create_production_storage
from chainura.telegram.redis_runtime import UpdateIdempotency

class Resolver:
    async def resolve(self,user_id): return None

class Callback:
    def __init__(self): self.answer=AsyncMock()

class MiddlewareLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_aiogram_330_and_exact_manual_order(self):
        redis=Redis.from_url('redis://localhost:6379/0')
        storage=create_production_storage(redis,'identity')
        dispatcher=build_dispatcher(storage,UpdateIdempotency(redis,'identity'),Resolver())
        middlewares=dispatcher.update.outer_middleware._middlewares
        self.assertIsInstance(middlewares[0],ErrorsMiddleware)
        self.assertIsInstance(middlewares[1],UserContextMiddleware)
        self.assertEqual([type(x) for x in middlewares[2:]], [TelegramErrorBoundary,
            PrivateChatGate,IdempotencyMiddleware,FSMContextMiddleware,ActorResolutionMiddleware])
        self.assertIsInstance(dispatcher.storage,MemoryStorage)
        self.assertIsNot(dispatcher.storage,storage)
        production=middlewares[5]
        self.assertIs(production.storage,storage)
        self.assertIsInstance(production.events_isolation,DisabledEventIsolation)
        await storage.close()
    async def test_callback_acknowledgement_is_at_most_once(self):
        callback=Callback(); ack=CallbackAcknowledger(callback)
        self.assertTrue(await ack.answer_once('one')); self.assertFalse(await ack.answer_once('two'))
        callback.answer.assert_awaited_once_with(text='one')
    async def test_failed_ack_is_not_retried(self):
        callback=Callback(); callback.answer.side_effect=RuntimeError('failed')
        ack=CallbackAcknowledger(callback)
        with self.assertRaises(RuntimeError): await ack.answer_once('one')
        self.assertFalse(await ack.answer_once('two'))
        callback.answer.assert_awaited_once()

class Service:
    def __init__(self): self.calls=[]
    async def claim(self,update_id):
        from chainura.telegram.redis_runtime import ClaimResult
        self.calls.append('claim'); return ClaimResult.CLAIMED,'owner'
    async def finish(self,update_id,owner): self.calls.append('finish'); return True
    async def release(self,update_id,owner): self.calls.append('release'); return True

class Event:
    update_id=8

class MiddlewareFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_downstream_failure_releases_without_processed_marker(self):
        service=Service(); middleware=IdempotencyMiddleware(service)
        async def fail(event,data): raise RuntimeError('fsm failed')
        with self.assertRaisesRegex(RuntimeError,'fsm failed'):
            await middleware(fail,Event(),{'callback_ack':None})
        self.assertEqual(service.calls,['claim','release'])
    async def test_success_finalizes(self):
        service=Service(); middleware=IdempotencyMiddleware(service)
        async def succeed(event,data): return 'ok'
        self.assertEqual(await middleware(succeed,Event(),{'callback_ack':None}),'ok')
        self.assertEqual(service.calls,['claim','finish'])
