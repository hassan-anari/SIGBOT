import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from integration_tests.support import create_safe_engine, migrate, require_safe_test_database
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.application.warmup import WarmupService
from chainura.domain.message_template import DestinationMessageKind, MessageTemplateSource
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import (DestinationRecord, DestinationSignalistRecord,
    DestinationWarmupCooldownRecord, SignalistRecord, WarmupPublicationRecord,
    WarmupRequestRecord)
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory


class MutableClock:
    def __init__(self): self.now = datetime(2026, 8, 13, 10, 17, 30, tzinfo=timezone.utc)
    def __call__(self): return self.now


class WarmupIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.safe = require_safe_test_database(); migrate("head", self.safe)
        self.engine = create_safe_engine(self.safe)
        self.factory = SqlAlchemySignalistTransactionFactory(create_session_factory(self.engine))
        self.clock = MutableClock()
        async with self.factory() as tx:
            tx._session.add_all([
                SignalistRecord(id=1, telegram_user_id=101, display_name="A", status="active"),
                SignalistRecord(id=2, telegram_user_id=102, display_name="B", status="active"),
                DestinationRecord(id=1, internal_name="X", telegram_chat_id=-1,
                    destination_type="channel", access_level="public", status="active", daily_limit=1),
                DestinationRecord(id=2, internal_name="Y", telegram_chat_id=-2,
                    destination_type="channel", access_level="public", status="active", daily_limit=1),
                DestinationRecord(id=3, internal_name="Z", telegram_chat_id=-3,
                    destination_type="channel", access_level="public", status="inactive", daily_limit=1),
            ])
            await tx._session.flush()
            tx._session.add_all([
                DestinationSignalistRecord(signalist_id=1, destination_id=1),
                DestinationSignalistRecord(signalist_id=2, destination_id=1),
                DestinationSignalistRecord(signalist_id=2, destination_id=2),
                DestinationSignalistRecord(signalist_id=2, destination_id=3),
            ]); await tx.commit()

    async def asyncTearDown(self):
        await self.engine.dispose(); self.safe.cleanup()

    async def count(self, model):
        async with self.factory() as tx:
            value = await tx._session.scalar(select(func.count()).select_from(model)); await tx.commit()
        return value

    async def test_rolling_boundary_and_inactive_destination(self):
        service = WarmupService(self.factory, clock=self.clock)
        self.assertEqual(await service.trigger(1), 1)
        self.assertEqual(await service.trigger(1), 0)
        self.clock.now += timedelta(minutes=59)
        self.assertEqual(await service.trigger(1), 0)
        self.clock.now += timedelta(minutes=1)
        self.assertEqual(await service.trigger(1), 1)
        self.clock.now += timedelta(minutes=61)
        self.assertEqual(await service.trigger(1), 1)
        self.assertEqual(await self.count(WarmupPublicationRecord), 3)
        self.assertEqual(await self.count(DestinationWarmupCooldownRecord), 1)

    async def test_partial_overlap_and_zero_rows_request(self):
        service = WarmupService(self.factory, clock=self.clock)
        self.assertEqual(await service.trigger(1), 1)
        self.clock.now += timedelta(minutes=1)
        self.assertEqual(await service.trigger(2), 1)  # X blocked, Y accepted, Z inactive.
        self.assertEqual(await self.count(WarmupPublicationRecord), 2)
        self.assertEqual(await self.count(WarmupRequestRecord), 2)

    async def test_true_concurrency_uses_database_cas(self):
        async with self.factory() as tx:
            await tx._session.execute(delete(DestinationSignalistRecord).where(
                DestinationSignalistRecord.signalist_id == 2,
                DestinationSignalistRecord.destination_id != 1)); await tx.commit()
        first = WarmupService(self.factory, clock=self.clock)
        second = WarmupService(self.factory, clock=self.clock)
        results = await asyncio.gather(first.trigger(1), second.trigger(2))
        self.assertEqual(sum(results), 1)
        self.assertEqual(await self.count(WarmupPublicationRecord), 1)

    async def test_static_template_precedence_and_limit(self):
        templates = DestinationMessageTemplateService(self.factory)
        built_in = await templates.resolve(DestinationMessageKind.WARMUP,
                                           destination_id=1, context={})
        self.assertEqual(built_in.source, MessageTemplateSource.BUILTIN)
        await templates.set_global(DestinationMessageKind.WARMUP, "global https://example.com")
        await templates.set_destination(DestinationMessageKind.WARMUP, 1, "specific")
        self.assertEqual((await templates.resolve(DestinationMessageKind.WARMUP,
                                                  destination_id=1, context={})).text, "specific")
        await templates.remove_destination(DestinationMessageKind.WARMUP, 1)
        self.assertIn("https://example.com", (await templates.resolve(
            DestinationMessageKind.WARMUP, destination_id=1, context={})).text)
        with self.assertRaises(Exception): templates.preview(DestinationMessageKind.WARMUP, "{symbol}")
        with self.assertRaises(Exception): templates.preview(DestinationMessageKind.WARMUP, "x" * 4097)
