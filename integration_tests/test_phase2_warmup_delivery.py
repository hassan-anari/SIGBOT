import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from integration_tests.support import create_safe_engine, migrate, require_safe_test_database
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.application.publication import DeliveryAmbiguous, DeliveryRateLimited, DeliveryRejected
from chainura.application.warmup import WarmupPublicationService, WarmupPublicationWorker, WarmupService
from chainura.domain.publication import PublicationStatus
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import (DestinationRecord, DestinationSignalistRecord,
    DestinationWarmupCooldownRecord, SignalistRecord, WarmupPublicationRecord)
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory


class Clock:
    def __init__(self): self.now = datetime(2026, 8, 14, 9, 0, tzinfo=timezone.utc)
    def __call__(self): return self.now


class Publisher:
    def __init__(self, outcomes, factory=None):
        self.outcomes = list(outcomes); self.calls = []; self.factory = factory
        self.observed = []

    async def send_standalone_text(self, chat_id, text):
        self.calls.append((chat_id, text))
        if self.factory:
            async with self.factory() as tx:
                row = await tx._session.get(WarmupPublicationRecord, 1)
                self.observed.append((row.status, row.attempt_count)); await tx.commit()
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException): raise outcome
        return outcome


class SignallingPublisher(Publisher):
    def __init__(self, outcomes):
        super().__init__(outcomes); self.sent = __import__("asyncio").Event()

    async def send_standalone_text(self, chat_id, text):
        result = await super().send_standalone_text(chat_id, text)
        self.sent.set(); return result


class BrokenTemplates:
    async def resolve(self, *args, **kwargs): raise ValueError("invalid template")


class CrashingTemplates:
    async def resolve(self, *args, **kwargs): raise KeyboardInterrupt()


class WarmupDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.safe = require_safe_test_database(); migrate("head", self.safe)
        self.engine = create_safe_engine(self.safe)
        self.factory = SqlAlchemySignalistTransactionFactory(create_session_factory(self.engine))
        self.clock = Clock()
        async with self.factory() as tx:
            tx._session.add_all([
                SignalistRecord(id=1, telegram_user_id=1001, display_name="Signalist", status="active"),
                DestinationRecord(id=1, internal_name="Secret Destination", telegram_chat_id=-100123,
                    destination_type="channel", access_level="public", status="active", daily_limit=1),
            ]); await tx._session.flush()
            tx._session.add(DestinationSignalistRecord(signalist_id=1, destination_id=1)); await tx.commit()
        self.planner = WarmupService(self.factory, clock=self.clock)
        self.assertEqual(await self.planner.trigger(1), 1)

    async def asyncTearDown(self):
        await self.engine.dispose(); self.safe.cleanup()

    async def state(self):
        async with self.factory() as tx:
            publication = await tx._session.get(WarmupPublicationRecord, 1)
            cooldown = await tx._session.get(DestinationWarmupCooldownRecord, 1)
            result = (publication.status, publication.attempt_count, publication.retry_at,
                      publication.telegram_message_id, publication.sent_at,
                      cooldown.reserved_at, cooldown.next_allowed_at)
            await tx.commit(); return result

    async def service(self, publisher, templates=None, **kwargs):
        return WarmupPublicationService(self.factory, publisher,
            templates or DestinationMessageTemplateService(self.factory), clock=self.clock, **kwargs)

    async def test_success_claims_before_external_send_and_persists_result(self):
        before = await self.state(); publisher = Publisher([321], self.factory)
        service = await self.service(publisher)
        await service.process(1)
        after = await self.state()
        self.assertEqual(publisher.observed, [(PublicationStatus.SENDING, 1)])
        self.assertEqual(after[0], PublicationStatus.SENT); self.assertEqual(after[1], 1)
        self.assertEqual(after[3], 321); self.assertEqual(after[4], self.clock.now)
        self.assertEqual(after[5:], before[5:])

    async def test_worker_wake_processes_durable_pending_row(self):
        publisher = SignallingPublisher([654]); service = await self.service(publisher)
        worker = WarmupPublicationWorker(service, concurrency=1, sweep_seconds=300)
        await worker.start(); worker.wake()
        await __import__("asyncio").wait_for(publisher.sent.wait(), timeout=2)
        await worker.close()
        after = await self.state()
        self.assertEqual(after[0], PublicationStatus.SENT); self.assertEqual(after[3], 654)

    async def test_invalid_response_is_delivery_unknown(self):
        for invalid in (0, -1, "1", True):
            with self.subTest(invalid=invalid):
                publisher = Publisher([invalid]); service = await self.service(publisher)
                await service.process(1)
                self.assertEqual((await self.state())[0], PublicationStatus.DELIVERY_UNKNOWN)
                async with self.factory() as tx:
                    await tx._session.execute(update(WarmupPublicationRecord).where(
                        WarmupPublicationRecord.id == 1).values(
                        status=PublicationStatus.PENDING, attempt_count=0,
                        first_attempt_at=None, last_error_category=None)); await tx.commit()

    async def test_rate_limit_retries_same_row_without_extending_cooldown(self):
        before = await self.state(); publisher = Publisher([DeliveryRateLimited(7), DeliveryRateLimited(7)])
        service = await self.service(publisher, max_attempts=2, retry_base_seconds=1)
        await service.process(1); first = await self.state()
        self.assertEqual(first[0], PublicationStatus.RETRY_WAIT)
        self.assertEqual(first[2], self.clock.now + timedelta(seconds=7))
        self.assertEqual(first[5:], before[5:])
        self.clock.now = first[2]
        self.assertEqual(await service.pending_ids(), (1,))
        await service.process(1); final = await self.state()
        self.assertEqual(final[0], PublicationStatus.FAILED); self.assertEqual(final[1], 2)
        self.assertEqual(final[5:], before[5:])

    async def test_rejection_and_ambiguous_delivery_are_terminal_and_keep_cooldown(self):
        before = await self.state(); publisher = Publisher([DeliveryRejected("forbidden")])
        service = await self.service(publisher); await service.process(1)
        after = await self.state(); self.assertEqual(after[0], PublicationStatus.FAILED)
        self.assertEqual(after[5:], before[5:])

    async def test_ambiguous_is_not_scanned_or_resent(self):
        before = await self.state(); publisher = Publisher([DeliveryAmbiguous("timeout")])
        service = await self.service(publisher); await service.process(1)
        after = await self.state(); self.assertEqual(after[0], PublicationStatus.DELIVERY_UNKNOWN)
        self.assertEqual(after[5:], before[5:]); self.assertEqual(await service.pending_ids(), ())
        await service.process(1); self.assertEqual(len(publisher.calls), 1)

    async def test_restart_recovers_sending_without_resend(self):
        before = await self.state()
        async with self.factory() as tx:
            await tx._session.execute(update(WarmupPublicationRecord).values(
                status=PublicationStatus.SENDING, attempt_count=1)); await tx.commit()
        publisher = Publisher([1]); service = await self.service(publisher); await service.recover()
        after = await self.state(); self.assertEqual(after[0], PublicationStatus.DELIVERY_UNKNOWN)
        self.assertEqual(after[5:], before[5:]); self.assertEqual(await service.pending_ids(), ())
        await service.process(1); self.assertEqual(publisher.calls, [])

    async def test_template_failure_is_definite_and_never_calls_telegram(self):
        before = await self.state(); publisher = Publisher([1])
        service = await self.service(publisher, BrokenTemplates()); await service.process(1)
        after = await self.state(); self.assertEqual(after[0], PublicationStatus.FAILED)
        self.assertNotEqual(after[0], PublicationStatus.DELIVERY_UNKNOWN)
        self.assertEqual(publisher.calls, []); self.assertEqual(after[5:], before[5:])

    async def test_unexpected_failure_recovery_prevents_duplicate_send(self):
        before = await self.state(); publisher = Publisher([1])
        service = await self.service(publisher, CrashingTemplates())
        with self.assertRaises(KeyboardInterrupt): await service.process(1)
        self.assertEqual((await self.state())[0], PublicationStatus.SENDING)
        await service.recover_unexpected_failure(1)
        after = await self.state(); self.assertEqual(after[0], PublicationStatus.DELIVERY_UNKNOWN)
        self.assertEqual(after[5:], before[5:]); self.assertEqual(await service.pending_ids(), ())

    async def _assert_skip(self, mutation, expected):
        before = await self.state(); publisher = Publisher([1]); await mutation()
        service = await self.service(publisher); await service.process(1); after = await self.state()
        self.assertEqual(after[0], expected); self.assertEqual(publisher.calls, [])
        self.assertEqual(after[5:], before[5:])

    async def test_signalist_inactive_revalidation(self):
        async def mutate():
            async with self.factory() as tx:
                await tx._session.execute(update(SignalistRecord).where(SignalistRecord.id == 1).values(status="inactive")); await tx.commit()
        await self._assert_skip(mutate, PublicationStatus.SKIPPED_SIGNALIST_INACTIVE)

    async def test_destination_inactive_revalidation(self):
        async def mutate():
            async with self.factory() as tx:
                await tx._session.execute(update(DestinationRecord).where(DestinationRecord.id == 1).values(status="inactive")); await tx.commit()
        await self._assert_skip(mutate, PublicationStatus.SKIPPED_DESTINATION_INACTIVE)

    async def test_unassigned_revalidation(self):
        async def mutate():
            async with self.factory() as tx:
                await tx._session.execute(delete(DestinationSignalistRecord)); await tx.commit()
        await self._assert_skip(mutate, PublicationStatus.SKIPPED_UNASSIGNED)
