from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FlowIdentity:
    chat_id: int
    user_id: int


@dataclass(frozen=True, slots=True)
class FinalizationClaim:
    identity: FlowIdentity
    flow_token: str
    creation_token: str
    final_validation_token: str
    canonical_symbol: str


@dataclass(slots=True)
class _LockEntry:
    lock: asyncio.Lock
    references: int = 0


class Phase5Coordination:
    def __init__(self) -> None:
        self._locks: dict[FlowIdentity, _LockEntry] = {}
        self._claims: dict[FlowIdentity, FinalizationClaim] = {}

    @asynccontextmanager
    async def hold(self, identity: FlowIdentity):
        entry = self._locks.get(identity)
        if entry is None:
            entry = _LockEntry(asyncio.Lock())
            self._locks[identity] = entry
        entry.references += 1
        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            entry.references -= 1
            if entry.references == 0 and not entry.lock.locked():
                self._locks.pop(identity, None)

    def register_claim(self, claim: FinalizationClaim) -> None:
        self._claims[claim.identity] = claim

    def live_claim(self, identity: FlowIdentity) -> FinalizationClaim | None:
        return self._claims.get(identity)

    def remove_claim(self, claim: FinalizationClaim) -> None:
        if self._claims.get(claim.identity) == claim:
            self._claims.pop(claim.identity, None)

    def entry_count_for_tests(self) -> int:
        return len(self._locks)

    def claim_count_for_tests(self) -> int:
        return len(self._claims)


def identity_from_message(message) -> FlowIdentity:
    if message is None or message.chat is None or message.from_user is None:
        raise ValueError("message identity is unavailable")
    return FlowIdentity(chat_id=message.chat.id, user_id=message.from_user.id)


def identity_from_callback(callback) -> FlowIdentity:
    if callback is None or callback.message is None or callback.from_user is None:
        raise ValueError("callback identity is unavailable")
    return FlowIdentity(chat_id=callback.message.chat.id, user_id=callback.from_user.id)


phase5_coordination = Phase5Coordination()
