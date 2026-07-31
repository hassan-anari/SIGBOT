import unittest
from chainura.telegram.redis_runtime import PollerLease, UpdateIdempotency, ClaimResult, bot_identity

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
