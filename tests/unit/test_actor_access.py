from __future__ import annotations
import unittest
from datetime import datetime, timezone
from chainura.application.access import ActorKind, ActorResolver
from chainura.config import ApplicationSettings
from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.errors import SignalistNotFoundError, SignalistPersistenceError, ActorResolutionError


def row(status: SignalistStatus) -> Signalist:
    now=datetime.now(timezone.utc)
    return Signalist.rehydrate(id=1, telegram_user_id=2, display_name="نام", telegram_username=None,
        status=status, created_at=now, updated_at=now,
        archived_at=now if status is SignalistStatus.ARCHIVED else None)

class Service:
    def __init__(self, value=None, error=None): self.value=value; self.error=error; self.calls=0
    async def get_by_telegram_user_id(self, user_id):
        self.calls += 1
        if self.error: raise self.error
        return self.value

class ActorResolverTests(unittest.IsolatedAsyncioTestCase):
    def settings(self): return ApplicationSettings("sqlite+aiosqlite:///./data/test.db", 1)
    async def test_admin_precedence_skips_service(self):
        service=Service(); actor=await ActorResolver(self.settings(), service).resolve(1)
        self.assertIs(actor.kind, ActorKind.MAIN_ADMIN); self.assertEqual(service.calls, 0)
    async def test_all_signalist_statuses(self):
        for status, kind in [(SignalistStatus.ACTIVE, ActorKind.ACTIVE_SIGNALIST),
            (SignalistStatus.INACTIVE, ActorKind.INACTIVE_SIGNALIST),
            (SignalistStatus.ARCHIVED, ActorKind.ARCHIVED_SIGNALIST)]:
            with self.subTest(status=status):
                actor=await ActorResolver(self.settings(), Service(row(status))).resolve(2)
                self.assertIs(actor.kind, kind)
    async def test_unknown_and_failure_are_distinct(self):
        actor=await ActorResolver(self.settings(), Service(error=SignalistNotFoundError())).resolve(2)
        self.assertIs(actor.kind, ActorKind.UNKNOWN)
        with self.assertRaises(ActorResolutionError):
            await ActorResolver(self.settings(), Service(error=SignalistPersistenceError())).resolve(2)
