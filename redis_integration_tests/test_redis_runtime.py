import asyncio
import unittest
from aiogram.fsm.storage.base import StorageKey
from chainura.telegram.redis_runtime import PollerLease,UpdateIdempotency,ClaimResult,bot_identity
from chainura.telegram.runtime import create_production_storage
from redis_integration_tests.support import require_safe_test_redis,create_client,cleanup

class RedisRuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls): cls.safe=require_safe_test_redis()
    async def asyncSetUp(self): self.redis=create_client(self.safe); await self.redis.ping()
    async def asyncTearDown(self): await cleanup(self.redis,self.safe); await self.redis.aclose(close_connection_pool=True)
    async def test_lease_exclusivity_ownership_and_expiration(self):
        first=PollerLease(self.redis,'bot',namespace=self.safe.namespace)
        second=PollerLease(self.redis,'bot',namespace=self.safe.namespace)
        self.assertTrue(await first.acquire()); self.assertFalse(await second.acquire())
        self.assertTrue(await first.renew()); self.assertFalse(await second.release())
        self.assertTrue(await first.release())
        await self.redis.set(first.key,'dead',px=30); await asyncio.sleep(.05)
        self.assertIsNone(await self.redis.get(first.key))
    async def test_idempotency_claim_finish_and_retry(self):
        service=UpdateIdempotency(self.redis,'bot',namespace=self.safe.namespace)
        result,owner=await service.claim(7); self.assertIs(result,ClaimResult.CLAIMED)
        duplicate,_=await service.claim(7); self.assertIs(duplicate,ClaimResult.PROCESSING)
        self.assertTrue(await service.release(7,owner))
        retry,owner=await service.claim(7); self.assertIs(retry,ClaimResult.CLAIMED)
        self.assertTrue(await service.finish(7,owner))
        processed,_=await service.claim(7); self.assertIs(processed,ClaimResult.PROCESSED)
    async def test_actual_fsm_namespace_round_trip_and_ttl(self):
        token='123456:secret-token'; identity=bot_identity(token)
        test_identity=f"{self.safe.namespace.removeprefix('chainura:tg:')}:{identity}"
        storage=create_production_storage(self.redis,test_identity)
        key=StorageKey(bot_id=1,chat_id=2,user_id=2)
        await storage.set_state(key,'phase:test'); await storage.set_data(key,{'temporary':True})
        self.assertEqual(await storage.get_state(key),'phase:test')
        self.assertEqual(await storage.get_data(key),{'temporary':True})
        keys=[key async for key in self.redis.scan_iter(match=f'{self.safe.namespace}:*')]
        self.assertTrue(keys); self.assertTrue(all(token not in item for item in keys))
        ttls=[await self.redis.ttl(item) for item in keys]
        self.assertTrue(all(ttl > 0 for ttl in ttls))
        await storage.close()
        self.redis=create_client(self.safe)
    async def asyncTearDown(self):
        if self.redis is not None:
            await cleanup(self.redis,self.safe); await self.redis.aclose(close_connection_pool=True)
