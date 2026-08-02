import asyncio
import unittest
from types import SimpleNamespace

from chainura.telegram.phase5_coordination import (FlowIdentity, Phase5Coordination,
    identity_from_callback, identity_from_message)

class IdentityTests(unittest.TestCase):
 def test_real_callback_uses_clicking_user_not_bot_sender(self):
  message=SimpleNamespace(chat=SimpleNamespace(id=10),from_user=SimpleNamespace(id=999))
  callback=SimpleNamespace(message=message,from_user=SimpleNamespace(id=20))
  self.assertEqual(identity_from_message(SimpleNamespace(chat=message.chat,from_user=callback.from_user)),FlowIdentity(10,20))
  self.assertEqual(identity_from_callback(callback),FlowIdentity(10,20))
  self.assertNotEqual(identity_from_callback(callback).user_id,message.from_user.id)
 def test_same_user_callbacks_match_and_different_users_do_not(self):
  message=SimpleNamespace(chat=SimpleNamespace(id=10),from_user=SimpleNamespace(id=999))
  first=SimpleNamespace(message=message,from_user=SimpleNamespace(id=20))
  second=SimpleNamespace(message=message,from_user=SimpleNamespace(id=20))
  other=SimpleNamespace(message=message,from_user=SimpleNamespace(id=21))
  self.assertEqual(identity_from_callback(first),identity_from_callback(second))
  self.assertNotEqual(identity_from_callback(first),identity_from_callback(other))

class LockManagerTests(unittest.IsolatedAsyncioTestCase):
 async def test_waiters_share_lock_and_registry_cleans_up(self):
  manager=Phase5Coordination(); identity=FlowIdentity(1,2); entered=asyncio.Event(); release=asyncio.Event(); second_entered=asyncio.Event(); second_started=asyncio.Event()
  async def first():
   async with manager.hold(identity): entered.set(); await release.wait()
  async def second():
   second_started.set()
   async with manager.hold(identity): second_entered.set()
  one=asyncio.create_task(first()); await entered.wait(); two=asyncio.create_task(second()); await second_started.wait()
  self.assertEqual(manager.entry_count_for_tests(),1); self.assertFalse(second_entered.is_set())
  release.set(); await asyncio.gather(one,two); self.assertEqual(manager.entry_count_for_tests(),0)
 async def test_cancelled_waiter_and_holder_do_not_leak(self):
  manager=Phase5Coordination(); identity=FlowIdentity(1,2); entered=asyncio.Event(); release=asyncio.Event()
  async def holder():
   async with manager.hold(identity): entered.set(); await release.wait()
  one=asyncio.create_task(holder()); await entered.wait()
  started=asyncio.Event(); waiter=asyncio.create_task(self._wait(manager,identity,started)); await started.wait(); waiter.cancel()
  with self.assertRaises(asyncio.CancelledError): await waiter
  release.set(); await one; self.assertEqual(manager.entry_count_for_tests(),0)
 async def _wait(self,manager,identity,started):
  started.set()
  async with manager.hold(identity): pass
