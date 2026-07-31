import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from chainura.errors import TelegramRuntimeError
from chainura.telegram.redis_runtime import (LEASE_RENEW_SECONDS, LEASE_TTL_MS,
    RENEW_COMMAND_TIMEOUT_SECONDS, PollerLease, UpdateIdempotency, ClaimResult, bot_identity)

class FakeRedis:
    def __init__(self): self.values={}; self.expiry={}
    async def set(self,key,value,**kwargs):
        if kwargs.get('nx') and key in self.values: return False
        self.values[key]=str(value); self.expiry[key]=kwargs.get('px') or kwargs.get('ex'); return True
    async def eval(self,script,n,*args):
        keys=args[:n]; argv=args[n:]
        if 'psetex' in script:
            if keys[1] in self.values:return 2
            if keys[0] in self.values:return 1
            self.values[keys[0]]=str(argv[0]); self.expiry[keys[0]]=int(argv[1]); return 0
        if "KEYS[3]" in script:
            if self.values.get(keys[0]) != str(argv[0]): return 0
            self.values[keys[1]]='1'; self.expiry[keys[1]]=int(argv[1]); del self.values[keys[0]]
            self.values[keys[2]]=str(argv[2]); self.expiry[keys[2]]=int(argv[3]); return 1
        if 'pexpire' in script:
            if self.values.get(keys[0]) != str(argv[0]): return 0
            self.expiry[keys[0]]=int(argv[1]); return 1
        if self.values.get(keys[0]) != str(argv[0]): return 0
        del self.values[keys[0]]; return 1

class RedisRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_renewal_deadline_has_lease_expiry_margin(self):
        self.assertLess(
            (LEASE_RENEW_SECONDS + RENEW_COMMAND_TIMEOUT_SECONDS) * 1000,
            LEASE_TTL_MS,
        )

    async def test_owner_safe_lease(self):
        redis=FakeRedis(); first=PollerLease(redis,'abc'); second=PollerLease(redis,'abc')
        self.assertTrue(await first.acquire()); self.assertFalse(await second.acquire())
        self.assertTrue(await first.renew()); self.assertFalse(await second.renew())
        self.assertFalse(await second.release()); self.assertTrue(await first.release())
        self.assertNotIn('secret-token', PollerLease(redis,bot_identity('secret-token')).key)
    async def test_claim_duplicate_finish_and_release(self):
        redis=FakeRedis(); service=UpdateIdempotency(redis,'abc')
        result,owner=await service.claim(4); self.assertIs(result,ClaimResult.CLAIMED)
        duplicate,_=await service.claim(4); self.assertIs(duplicate,ClaimResult.PROCESSING)
        self.assertTrue(await service.finish(4,owner))
        processed,_=await service.claim(4); self.assertIs(processed,ClaimResult.PROCESSED)
        result,owner=await service.claim(5); self.assertTrue(await service.release(5,owner))
        self.assertNotIn(service.keys(5)[0],redis.values)

    async def test_every_renewal_failure_sets_fatal_loss(self):
        for failure in (False, TelegramRuntimeError('safe'), RuntimeError('unexpected')):
            lease=PollerLease(FakeRedis(),'abc'); lost=asyncio.Event(); stop=asyncio.Event()
            lease.renew=AsyncMock(return_value=failure) if failure is False else AsyncMock(side_effect=failure)
            with self.subTest(failure=type(failure).__name__), patch(
                'chainura.telegram.redis_runtime.LEASE_RENEW_SECONDS', 0
            ), self.assertRaises(TelegramRuntimeError):
                await lease.run_renewal(stop,lost)
            self.assertTrue(lost.is_set())

    async def test_hanging_renewal_times_out_as_fatal_lease_loss(self):
        lease=PollerLease(FakeRedis(),'abc'); lost=asyncio.Event(); stop=asyncio.Event()
        never=asyncio.Event()
        lease.renew=AsyncMock(side_effect=never.wait)
        with patch('chainura.telegram.redis_runtime.LEASE_RENEW_SECONDS',0), patch(
            'chainura.telegram.redis_runtime.RENEW_COMMAND_TIMEOUT_SECONDS',.01
        ), self.assertRaises(TelegramRuntimeError) as caught:
            await lease.run_renewal(stop,lost)
        self.assertTrue(lost.is_set())
        self.assertIsInstance(caught.exception.__cause__, TimeoutError)
