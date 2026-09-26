from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.application.publication import DeliveryAmbiguous, DeliveryRateLimited, DeliveryRejected
from chainura.domain.destination import DestinationStatus
from chainura.domain.message_template import DestinationMessageKind
from chainura.domain.publication import PublicationStatus
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.models import (
    DestinationRecord,
    DestinationSignalistRecord,
    DestinationWarmupCooldownRecord,
    SignalistRecord,
    WarmupPublicationRecord,
    WarmupRequestRecord,
)

WARMUP_COOLDOWN_SECONDS = 3600


def _aware(clock):
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return an aware datetime")
    return value


class WarmupService:
    """Atomically records one trigger and reserves each eligible destination."""

    def __init__(self, transactions, *, clock=lambda: datetime.now(timezone.utc), wake=None):
        self.transactions = transactions
        self.clock = clock
        self.wake = wake

    async def trigger(self, signalist_id: int) -> int:
        now = _aware(self.clock)
        next_allowed = now + timedelta(seconds=WARMUP_COOLDOWN_SECONDS)
        async with self.transactions() as tx:
            actor = await tx._session.get(SignalistRecord, signalist_id)
            if actor is None or actor.status is not SignalistStatus.ACTIVE:
                raise PermissionError("active Signalist required")
            request = WarmupRequestRecord(signalist_id=signalist_id, created_at=now, updated_at=now)
            tx._session.add(request)
            await tx._session.flush()
            destination_ids = tuple((await tx._session.scalars(
                select(DestinationRecord.id)
                .join(DestinationSignalistRecord,
                      DestinationSignalistRecord.destination_id == DestinationRecord.id)
                .where(DestinationSignalistRecord.signalist_id == signalist_id,
                       DestinationRecord.status == DestinationStatus.ACTIVE)
                .order_by(DestinationRecord.id)
            )).all())
            planned = 0
            for destination_id in destination_ids:
                statement = sqlite_insert(DestinationWarmupCooldownRecord).values(
                    destination_id=destination_id, last_request_id=request.id,
                    reserved_at=now, next_allowed_at=next_allowed, updated_at=now,
                ).on_conflict_do_update(
                    index_elements=[DestinationWarmupCooldownRecord.destination_id],
                    set_={"last_request_id": request.id, "reserved_at": now,
                          "next_allowed_at": next_allowed, "updated_at": now},
                    where=DestinationWarmupCooldownRecord.next_allowed_at <= now,
                )
                result = await tx._session.execute(statement)
                if result.rowcount == 1:
                    tx._session.add(WarmupPublicationRecord(
                        request_id=request.id, destination_id=destination_id,
                        status=PublicationStatus.PENDING, attempt_count=0,
                        created_at=now, updated_at=now,
                    ))
                    planned += 1
            await tx.commit()
        if self.wake is not None:
            self.wake()
        return planned


class WarmupPublicationService:
    def __init__(self, transactions, publisher, message_templates: DestinationMessageTemplateService,
                 *, clock=lambda: datetime.now(timezone.utc), max_attempts=3,
                 retry_base_seconds=.5):
        self.transactions = transactions
        self.publisher = publisher
        self.message_templates = message_templates
        self.clock = clock
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds

    async def recover(self):
        now = _aware(self.clock)
        async with self.transactions() as tx:
            await tx._session.execute(update(WarmupPublicationRecord).where(
                WarmupPublicationRecord.status == PublicationStatus.SENDING).values(
                status=PublicationStatus.DELIVERY_UNKNOWN,
                last_error_category="restart_during_send", updated_at=now))
            await tx.commit()

    async def pending_ids(self):
        now = _aware(self.clock)
        async with self.transactions() as tx:
            ids = tuple((await tx._session.scalars(select(WarmupPublicationRecord.id).where(or_(
                WarmupPublicationRecord.status == PublicationStatus.PENDING,
                and_(WarmupPublicationRecord.status == PublicationStatus.RETRY_WAIT,
                     WarmupPublicationRecord.retry_at <= now),
            )).order_by(WarmupPublicationRecord.id))).all())
            await tx.commit()
        return ids

    async def process(self, publication_id: int):
        now = _aware(self.clock)
        async with self.transactions() as tx:
            row = (await tx._session.execute(select(
                WarmupPublicationRecord, WarmupRequestRecord.signalist_id,
                DestinationRecord.telegram_chat_id, SignalistRecord.status,
                DestinationRecord.status,
                DestinationSignalistRecord.destination_id,
            ).join(WarmupRequestRecord, WarmupRequestRecord.id == WarmupPublicationRecord.request_id)
             .join(SignalistRecord, SignalistRecord.id == WarmupRequestRecord.signalist_id)
             .join(DestinationRecord, DestinationRecord.id == WarmupPublicationRecord.destination_id)
             .outerjoin(DestinationSignalistRecord, and_(
                 DestinationSignalistRecord.destination_id == WarmupPublicationRecord.destination_id,
                 DestinationSignalistRecord.signalist_id == WarmupRequestRecord.signalist_id))
             .where(WarmupPublicationRecord.id == publication_id,
                    WarmupPublicationRecord.status.in_((PublicationStatus.PENDING,
                                                        PublicationStatus.RETRY_WAIT))))).one_or_none()
            if row is None:
                return
            publication, signalist_id, chat_id, signalist_status, destination_status, assignment = row
            if signalist_status is not SignalistStatus.ACTIVE:
                target = PublicationStatus.SKIPPED_SIGNALIST_INACTIVE
            elif destination_status is not DestinationStatus.ACTIVE:
                target = PublicationStatus.SKIPPED_DESTINATION_INACTIVE
            elif assignment is None:
                target = PublicationStatus.SKIPPED_UNASSIGNED
            else:
                target = PublicationStatus.SENDING
            values = {"status": target, "updated_at": now}
            if target is PublicationStatus.SENDING:
                values.update(attempt_count=publication.attempt_count + 1,
                              retry_at=None)
                if publication.first_attempt_at is None:
                    values["first_attempt_at"] = now
            changed = (await tx._session.execute(update(WarmupPublicationRecord).where(
                WarmupPublicationRecord.id == publication_id,
                WarmupPublicationRecord.status == publication.status).values(**values))).rowcount
            await tx.commit()
        if not changed or target is not PublicationStatus.SENDING:
            return
        attempts = publication.attempt_count + 1
        try:
            text = (await self.message_templates.resolve(
                DestinationMessageKind.WARMUP, destination_id=publication.destination_id,
                context={})).text
            message_id = await self.publisher.send_standalone_text(chat_id, text)
        except DeliveryRateLimited as exc:
            if attempts < self.max_attempts:
                retry_at = _aware(self.clock) + timedelta(seconds=max(
                    exc.retry_after, self.retry_base_seconds * (2 ** (attempts - 1))))
                return await self._finish(publication_id, PublicationStatus.RETRY_WAIT,
                                          "rate_limited", retry_at=retry_at)
            return await self._finish(publication_id, PublicationStatus.FAILED,
                                      "rate_limit_exhausted")
        except DeliveryRejected as exc:
            return await self._finish(publication_id, PublicationStatus.FAILED, str(exc))
        except DeliveryAmbiguous as exc:
            return await self._finish(publication_id, PublicationStatus.DELIVERY_UNKNOWN, str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return await self._finish(publication_id, PublicationStatus.DELIVERY_UNKNOWN,
                                      type(exc).__name__)
        await self._finish(publication_id, PublicationStatus.SENT, message_id=message_id)

    async def recover_unexpected_failure(self, publication_id):
        await self._finish(publication_id, PublicationStatus.DELIVERY_UNKNOWN,
                           "unexpected_processing_failure")

    async def _finish(self, publication_id, target, error=None, *, retry_at=None, message_id=None):
        now = _aware(self.clock)
        values = {"status": target, "last_error_category": error,
                  "retry_at": retry_at, "updated_at": now}
        if target is PublicationStatus.SENT:
            if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
                target = PublicationStatus.DELIVERY_UNKNOWN
                values.update(status=target, last_error_category="invalid_response")
            else:
                values.update(telegram_message_id=message_id, sent_at=now)
        async with self.transactions() as tx:
            await tx._session.execute(update(WarmupPublicationRecord).where(
                WarmupPublicationRecord.id == publication_id,
                WarmupPublicationRecord.status == PublicationStatus.SENDING).values(**values))
            await tx.commit()


class WarmupPublicationWorker:
    def __init__(self, service, concurrency=3, sweep_seconds=5.0):
        self.service = service
        self.concurrency = concurrency
        self.sweep_seconds = sweep_seconds
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self.task = None

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    def wake(self):
        self._wake.set()

    async def _run(self):
        while not self._stop.is_set():
            self._wake.clear()
            ids = await self.service.pending_ids()
            for offset in range(0, len(ids), self.concurrency):
                batch = ids[offset:offset + self.concurrency]
                results = await asyncio.gather(*(self.service.process(item) for item in batch),
                                               return_exceptions=True)
                for item, result in zip(batch, results):
                    if isinstance(result, BaseException):
                        await self.service.recover_unexpected_failure(item)
            try:
                await asyncio.wait_for(self._wake.wait(), self.sweep_seconds)
            except asyncio.TimeoutError:
                pass

    async def close(self):
        self._stop.set()
        self._wake.set()
        if self.task is not None:
            await self.task
