from __future__ import annotations
import os
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from redis.asyncio import Redis

class UnsafeRedisTestConfiguration(RuntimeError): pass

@dataclass(frozen=True, slots=True)
class SafeTestRedis:
    url: str = field(repr=False)
    database: int
    namespace: str


def require_safe_test_redis(mapping=None) -> SafeTestRedis:
    environment=os.environ if mapping is None else mapping
    raw=environment.get('TEST_REDIS_URL')
    if not raw or not raw.strip(): raise UnsafeRedisTestConfiguration('TEST_REDIS_URL is required')
    try: parsed=urlsplit(raw.strip())
    except Exception as exc: raise UnsafeRedisTestConfiguration('TEST_REDIS_URL is invalid') from exc
    if parsed.scheme not in {'redis','rediss'}: raise UnsafeRedisTestConfiguration('TEST_REDIS_URL scheme is unsafe')
    if not parsed.hostname: raise UnsafeRedisTestConfiguration('TEST_REDIS_URL must name a host')
    path=parsed.path
    if not path or path=='/': raise UnsafeRedisTestConfiguration('TEST_REDIS_URL must select a database')
    try: database=int(path.removeprefix('/'))
    except ValueError as exc: raise UnsafeRedisTestConfiguration('TEST_REDIS_URL database is invalid') from exc
    if database == 0: raise UnsafeRedisTestConfiguration('TEST_REDIS_URL must not select database zero')
    if database < 0: raise UnsafeRedisTestConfiguration('TEST_REDIS_URL database is invalid')
    return SafeTestRedis(raw.strip(),database,f"chainura:tg:test:{uuid.uuid4().hex}")


def create_client(safe: SafeTestRedis) -> Redis:
    if not isinstance(safe,SafeTestRedis): raise UnsafeRedisTestConfiguration('validated Redis descriptor required')
    return Redis.from_url(safe.url,decode_responses=True)

async def cleanup(client: Redis,safe: SafeTestRedis) -> None:
    if not isinstance(safe,SafeTestRedis): raise UnsafeRedisTestConfiguration('validated Redis descriptor required')
    pattern=f"{safe.namespace}:*"
    async for key in client.scan_iter(match=pattern,count=100):
        if not key.startswith(f"{safe.namespace}:"):
            raise UnsafeRedisTestConfiguration('Redis cleanup escaped namespace')
        await client.delete(key)
