from __future__ import annotations

import asyncio
import hashlib
import secrets
from enum import Enum
from typing import Any

from chainura.errors import TelegramRuntimeError

LEASE_TTL_MS = 30_000
LEASE_RENEW_SECONDS = 10
PROCESSING_TTL_MS = 60_000
PROCESSED_TTL_SECONDS = 7 * 24 * 60 * 60
DIAGNOSTIC_TTL_SECONDS = 7 * 24 * 60 * 60
FSM_TTL_SECONDS = 24 * 60 * 60

OWNER_RENEW = """if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"""
OWNER_DELETE = """if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"""
CLAIM_UPDATE = """
if redis.call('exists', KEYS[2]) == 1 then return 2 end
if redis.call('exists', KEYS[1]) == 1 then return 1 end
redis.call('psetex', KEYS[1], ARGV[2], ARGV[1]); return 0
"""
FINISH_UPDATE = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('set', KEYS[2], '1', 'EX', ARGV[2])
redis.call('del', KEYS[1])
redis.call('set', KEYS[3], ARGV[3], 'EX', ARGV[4])
return 1
"""


def bot_identity(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class ClaimResult(str, Enum):
    CLAIMED = "claimed"
    PROCESSING = "processing"
    PROCESSED = "processed"


class PollerLease:
    def __init__(self, redis: Any, identity: str, *, namespace: str = "chainura") -> None:
        self.redis = redis
        self.key = f"{namespace}:tg:{identity}:poller"
        self.owner = secrets.token_urlsafe(24)

    async def acquire(self) -> bool:
        try:
            return bool(await self.redis.set(self.key, self.owner, nx=True, px=LEASE_TTL_MS))
        except Exception as exc:
            raise TelegramRuntimeError("poller lease unavailable") from exc

    async def renew(self) -> bool:
        try:
            return bool(await self.redis.eval(OWNER_RENEW, 1, self.key, self.owner, LEASE_TTL_MS))
        except Exception as exc:
            raise TelegramRuntimeError("poller lease renewal failed") from exc

    async def release(self) -> bool:
        try:
            return bool(await self.redis.eval(OWNER_DELETE, 1, self.key, self.owner))
        except Exception as exc:
            raise TelegramRuntimeError("poller lease release failed") from exc

    async def run_renewal(self, stop: asyncio.Event, lost: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), LEASE_RENEW_SECONDS)
                break
            except asyncio.TimeoutError:
                if not await self.renew():
                    lost.set()
                    return


class UpdateIdempotency:
    def __init__(self, redis: Any, identity: str, *, namespace: str = "chainura") -> None:
        self.redis = redis
        self.prefix = f"{namespace}:tg:{identity}"

    def keys(self, update_id: int) -> tuple[str, str]:
        base = f"{self.prefix}:update:{update_id}"
        return f"{base}:processing", f"{base}:processed"

    async def claim(self, update_id: int) -> tuple[ClaimResult, str]:
        owner = secrets.token_urlsafe(18)
        processing, processed = self.keys(update_id)
        try:
            result = int(await self.redis.eval(CLAIM_UPDATE, 2, processing, processed, owner, PROCESSING_TTL_MS))
        except Exception as exc:
            raise TelegramRuntimeError("update idempotency unavailable") from exc
        return ({0: ClaimResult.CLAIMED, 1: ClaimResult.PROCESSING, 2: ClaimResult.PROCESSED}[result], owner)

    async def finish(self, update_id: int, owner: str) -> bool:
        processing, processed = self.keys(update_id)
        diagnostic = f"{self.prefix}:last_processed"
        try:
            return bool(await self.redis.eval(FINISH_UPDATE, 3, processing, processed, diagnostic,
                                              owner, PROCESSED_TTL_SECONDS, update_id,
                                              DIAGNOSTIC_TTL_SECONDS))
        except Exception as exc:
            raise TelegramRuntimeError("update finalization failed") from exc

    async def release(self, update_id: int, owner: str) -> bool:
        processing, _ = self.keys(update_id)
        try:
            return bool(await self.redis.eval(OWNER_DELETE, 1, processing, owner))
        except Exception as exc:
            raise TelegramRuntimeError("update release failed") from exc
