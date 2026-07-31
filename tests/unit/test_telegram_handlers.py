import unittest
from unittest.mock import AsyncMock
from chainura.application.access import Actor,ActorKind
from chainura.telegram.routers import start,help_handler,menu_handler,callback_handler
from chainura.telegram.presentation import texts

class Message:
    def __init__(self): self.answer=AsyncMock()
class Ack:
    def __init__(self): self.answer_once=AsyncMock()
class Callback:
    def __init__(self,data='p3:menu'): self.data=data; self.message=None

class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_all_actor_categories(self):
        for kind in ActorKind:
            message=Message(); actor=Actor(kind, type('S',(),{'display_name':'نام'})() if kind is ActorKind.ACTIVE_SIGNALIST else None); await start(message,actor)
            message.answer.assert_awaited_once()
            args,kwargs=message.answer.await_args
            if kind in {ActorKind.INACTIVE_SIGNALIST,ActorKind.ARCHIVED_SIGNALIST,ActorKind.UNKNOWN}:
                self.assertIn(args[0],{texts.INACTIVE,texts.ARCHIVED,texts.UNKNOWN})
                self.assertIsNotNone(kwargs['reply_markup'])
    async def test_help_and_protected_denial(self):
        for function in (help_handler,menu_handler):
            message=Message(); await function(message,Actor(ActorKind.UNKNOWN))
            self.assertEqual(message.answer.await_args.args[0],texts.UNKNOWN)
    async def test_invalid_and_unauthorized_callbacks_ack_once(self):
        invalid=Callback('bad'); ack=Ack()
        await callback_handler(invalid,Actor(ActorKind.MAIN_ADMIN),ack)
        ack.answer_once.assert_awaited_once_with(texts.INVALID_CALLBACK)
        denied=Callback(); ack=Ack()
        await callback_handler(denied,Actor(ActorKind.ARCHIVED_SIGNALIST),ack)
        ack.answer_once.assert_awaited_once_with(texts.ARCHIVED)
