"""Durable text admission and pre-activation cancellation for Limit Signals."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime,timezone,timedelta
from sqlalchemy import delete,exists,func,or_,select,update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from chainura.application.message_templates import (limit_cancellation_context,
    limit_pending_context)
from chainura.application.publication import DeliveryAmbiguous,DeliveryRateLimited,DeliveryRejected
from chainura.domain.destination import DestinationAccessLevel,DestinationStatus
from chainura.domain.message_template import DestinationMessageKind
from chainura.domain.publication import PublicationStatus,select_initial_exchange,tehran_local_date
from chainura.domain.signal import SignalEntryType,SignalLifecycleStatus
from chainura.domain.signalist import SignalistStatus
from chainura.errors import TemplateNotConfiguredError
from chainura.persistence.models import (DestinationExchangePriorityRecord,DestinationRecord,
    DestinationSignalistRecord,DestinationTradeLockRecord,InitialPublicationDailySlotRecord,
    LimitAdmissionRequestRecord,LimitCancellationPublicationRecord,
    LimitPendingPublicationRecord,SignalInitialPriceRecord,SignalPositionStateRecord,
    SignalRecord,SignalistRecord)

LOGGER=logging.getLogger("chainura.limit_publication")
LOCKED=(PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.READY,
        PublicationStatus.SENDING,PublicationStatus.RETRY_WAIT,PublicationStatus.SENT,
        PublicationStatus.BLOCKED_TEMPLATE,PublicationStatus.DELIVERY_UNKNOWN)
RETRYABLE=(PublicationStatus.PENDING,PublicationStatus.RETRY_WAIT)

def _safe_error(exc):
    value=str(exc)
    return value if value in {"reply_parent_not_found","caption_too_long","message_invalid",
        "chat_not_found","insufficient_rights","telegram_bad_request","rate_limited",
        "delivery_ambiguous"} else "telegram_bad_request"

def _can_publish_pending(signal):
    return (signal is not None and signal.entry_type is SignalEntryType.LIMIT and
            (signal.status in (SignalLifecycleStatus.PENDING_ENTRY, SignalLifecycleStatus.ENTRY_TRIGGERED)
             or (signal.auto_activate_limit and signal.status is SignalLifecycleStatus.ACTIVE)))

class LimitPendingPublicationService:
    def __init__(self,transactions,publisher,message_templates,clock=lambda:datetime.now(timezone.utc),
                 max_attempts=3,retry_base_seconds=.5,activation_wake=None,
                 cancellation_wake=None):
        self.transactions=transactions;self.publisher=publisher;self.templates=message_templates
        self.clock=clock;self.max_attempts=max_attempts;self.retry_base=retry_base_seconds
        self.activation_wake=activation_wake
        self.cancellation_wake=cancellation_wake

    async def recover(self):
        now=self.clock()
        async with self.transactions() as tx:
            s=tx._session
            await s.execute(update(LimitAdmissionRequestRecord).where(
                LimitAdmissionRequestRecord.status=="planning").values(status="pending",updated_at=now))
            await s.execute(update(LimitPendingPublicationRecord).where(
                LimitPendingPublicationRecord.status.in_((PublicationStatus.RENDERING,PublicationStatus.READY))).values(
                status=PublicationStatus.PENDING,updated_at=now))
            await s.execute(update(LimitPendingPublicationRecord).where(
                LimitPendingPublicationRecord.status==PublicationStatus.SENDING).values(
                status=PublicationStatus.DELIVERY_UNKNOWN,last_error_category="restart_after_send_started",updated_at=now))
            rows=(await s.execute(select(LimitPendingPublicationRecord,SignalRecord).join(
                SignalRecord,SignalRecord.id==LimitPendingPublicationRecord.signal_id).where(
                LimitPendingPublicationRecord.status.in_(LOCKED)))).all()
            for publication,signal in rows:
                if signal.status is SignalLifecycleStatus.CANCELLED:
                    if publication.status not in (PublicationStatus.SENT,PublicationStatus.DELIVERY_UNKNOWN):
                        publication.status=PublicationStatus.SUPERSEDED
                        await self._release_admission(s,publication)
                    continue
                if signal.auto_activate_limit and signal.status is SignalLifecycleStatus.ACTIVE and (
                        publication.status in (PublicationStatus.SENT, PublicationStatus.DELIVERY_UNKNOWN)):
                    continue
                owner=await s.scalar(select(DestinationTradeLockRecord.signal_id).where(
                    DestinationTradeLockRecord.destination_id==publication.destination_id,
                    DestinationTradeLockRecord.canonical_symbol==signal.canonical_symbol))
                if owner is None:
                    await tx.publications.acquire_trade_lock(signal.id,publication.destination_id,
                                                              signal.canonical_symbol,now)
                elif owner!=signal.id:
                    publication.status=PublicationStatus.SUPERSEDED
                    publication.last_error_category="destination_trade_lock_occupied"
                    await self._release_slot(s,publication.id)
            await tx.commit()

    async def plan_pending(self,limit=50):
        async with self.transactions() as tx:
            ids=tuple((await tx._session.scalars(select(LimitAdmissionRequestRecord.id).where(
                LimitAdmissionRequestRecord.status=="pending").order_by(
                LimitAdmissionRequestRecord.created_at).limit(limit))).all());await tx.commit()
        for request_id in ids:await self._plan(request_id)

    async def _plan(self,request_id):
        now=self.clock();local_date=tehran_local_date(now)
        async with self.transactions() as tx:
            s=tx._session
            signal_id=(await s.execute(update(LimitAdmissionRequestRecord).where(
                LimitAdmissionRequestRecord.id==request_id,
                LimitAdmissionRequestRecord.status=="pending").values(
                status="planning",updated_at=now).returning(
                LimitAdmissionRequestRecord.signal_id))).scalar_one_or_none()
            if signal_id is None:await tx.commit();return
            signal=await s.get(SignalRecord,signal_id)
            position = await s.get(SignalPositionStateRecord, signal_id) if (
                signal and signal.auto_activate_limit and signal.status is SignalLifecycleStatus.ACTIVE) else None
            if not _can_publish_pending(signal) or (position and position.is_closed):
                await s.execute(update(LimitAdmissionRequestRecord).where(
                    LimitAdmissionRequestRecord.id==request_id).values(status="superseded",updated_at=now))
                await tx.commit();return
            sources=frozenset((await s.scalars(select(SignalInitialPriceRecord.exchange).where(
                SignalInitialPriceRecord.signal_id==signal_id))).all())
            rows=(await s.execute(select(DestinationRecord,DestinationExchangePriorityRecord.exchange).join(
                DestinationSignalistRecord,DestinationSignalistRecord.destination_id==DestinationRecord.id).join(
                DestinationExchangePriorityRecord,DestinationExchangePriorityRecord.destination_id==DestinationRecord.id).where(
                DestinationSignalistRecord.signalist_id==signal.signalist_id,
                DestinationRecord.status==DestinationStatus.ACTIVE).order_by(
                DestinationRecord.id,DestinationExchangePriorityRecord.position))).all()
            grouped={}
            for destination,exchange in rows:grouped.setdefault(destination.id,[destination,[]])[1].append(exchange)
            for destination,(record,priorities) in grouped.items():
                selected=select_initial_exchange(signal.mode,signal.selected_exchange,priorities,sources)
                if selected is None:continue
                admitted=await tx.publications.acquire_trade_lock(signal_id,destination,
                                                                   signal.canonical_symbol,now)
                publication=LimitPendingPublicationRecord(signal_id=signal_id,destination_id=destination,
                    selected_exchange=selected,status=(PublicationStatus.PENDING if admitted else PublicationStatus.SUPERSEDED),
                    last_error_category=None if admitted else "destination_trade_lock_occupied",
                    created_at=now,updated_at=now)
                s.add(publication);await s.flush()
                if admitted:
                    slot=await self._claim_capacity(s,publication.id,destination,local_date,record.daily_limit,now)
                    if slot is None:
                        publication.status=PublicationStatus.SKIPPED_DAILY_LIMIT
                        publication.last_error_category="daily_limit"
                        await tx.publications.release_trade_lock(signal_id,destination)
            await s.execute(update(LimitAdmissionRequestRecord).where(
                LimitAdmissionRequestRecord.id==request_id).values(status="planned",planned_at=now,updated_at=now))
            await tx.commit()

    async def _claim_capacity(self,s,publication_id,destination_id,local_date,daily_limit,now):
        used=set((await s.scalars(select(InitialPublicationDailySlotRecord.slot_number).where(
            InitialPublicationDailySlotRecord.destination_id==destination_id,
            InitialPublicationDailySlotRecord.local_date==local_date))).all())
        slot=next((number for number in range(1,daily_limit+1) if number not in used),None)
        if slot is None:return None
        s.add(InitialPublicationDailySlotRecord(destination_id=destination_id,local_date=local_date,
            slot_number=slot,publication_id=None,limit_publication_id=publication_id,created_at=now))
        await s.flush()
        return slot

    async def pending_ids(self,limit=100):
        now=self.clock()
        async with self.transactions() as tx:
            ids=tuple((await tx._session.scalars(select(LimitPendingPublicationRecord.id).where(or_(
                LimitPendingPublicationRecord.status==PublicationStatus.PENDING,
                (LimitPendingPublicationRecord.status==PublicationStatus.RETRY_WAIT)&
                (LimitPendingPublicationRecord.retry_at<=now))).order_by(
                LimitPendingPublicationRecord.id).limit(limit))).all());await tx.commit();return ids

    async def process(self,publication_id):
        now=self.clock()
        async with self.transactions() as tx:
            s=tx._session
            row=(await s.execute(update(LimitPendingPublicationRecord).where(
                LimitPendingPublicationRecord.id==publication_id,
                LimitPendingPublicationRecord.status.in_(RETRYABLE),
                or_(LimitPendingPublicationRecord.retry_at.is_(None),LimitPendingPublicationRecord.retry_at<=now)
            ).values(status=PublicationStatus.RENDERING,retry_at=None,updated_at=now).returning(
                LimitPendingPublicationRecord.signal_id,LimitPendingPublicationRecord.destination_id,
                LimitPendingPublicationRecord.attempt_count))).one_or_none()
            if row is None:await tx.commit();return
            signal_id,destination_id,attempts=row
            signal=await tx.signals.get_by_id(signal_id);destination=await s.get(DestinationRecord,destination_id)
            await tx.commit()
        try:
            if signal is None or destination is None:raise ValueError("missing_record")
            text=(await self.templates.resolve(DestinationMessageKind.LIMIT_PENDING,
                destination_id=destination_id,context=limit_pending_context(signal,
                private=destination.access_level is DestinationAccessLevel.PRIVATE))).text
        except TemplateNotConfiguredError:
            await self._transition(publication_id,PublicationStatus.RENDERING,
                PublicationStatus.BLOCKED_TEMPLATE,"template_missing");return
        except Exception:
            await self._definite_no_send(publication_id,"template_invalid");return
        async with self.transactions() as tx:
            s=tx._session
            # Serialize the final eligibility check with Trigger, cancellation and
            # terminal monitoring. Persist SENDING before any Telegram call.
            claimed = (await s.execute(update(LimitPendingPublicationRecord).where(
                LimitPendingPublicationRecord.id == publication_id,
                LimitPendingPublicationRecord.status == PublicationStatus.RENDERING,
            ).values(updated_at=now))).rowcount
            if not claimed:
                await tx.commit()
                return
            publication=await s.get(LimitPendingPublicationRecord,publication_id)
            db_signal=await s.get(SignalRecord,signal_id);actor=await s.get(SignalistRecord,db_signal.signalist_id) if db_signal else None
            db_destination=await s.get(DestinationRecord,destination_id)
            assigned=bool(db_signal and await s.scalar(select(exists().where(
                DestinationSignalistRecord.destination_id==destination_id,
                DestinationSignalistRecord.signalist_id==db_signal.signalist_id))) )
            lock_owner=(await s.scalar(select(DestinationTradeLockRecord.signal_id).where(
                DestinationTradeLockRecord.destination_id==destination_id,
                DestinationTradeLockRecord.canonical_symbol==db_signal.canonical_symbol))) if db_signal else None
            valid=(_can_publish_pending(db_signal) and
                actor is not None and actor.status is SignalistStatus.ACTIVE and db_destination is not None and
                db_destination.status is DestinationStatus.ACTIVE and assigned and lock_owner==signal_id)
            if not valid:
                publication.status=PublicationStatus.SUPERSEDED;publication.updated_at=now
                await self._release_admission(s,publication)
                await tx.commit();return
            publication.status=PublicationStatus.SENDING;publication.attempt_count+=1
            publication.updated_at=now;chat_id=db_destination.telegram_chat_id;attempts=publication.attempt_count
            await tx.commit()
        try:message_id=await self.publisher.send_standalone_text(chat_id,text)
        except DeliveryRateLimited as exc:
            retry=self.clock()+timedelta(seconds=max(exc.retry_after,self.retry_base*(2**max(0,attempts-1))))
            await self._transition(publication_id,PublicationStatus.SENDING,PublicationStatus.RETRY_WAIT,
                                   "rate_limited",retry);return
        except DeliveryRejected as exc:
            await self._definite_no_send(publication_id,_safe_error(exc),PublicationStatus.SENDING);return
        except DeliveryAmbiguous:
            await self._transition(publication_id,PublicationStatus.SENDING,
                                   PublicationStatus.DELIVERY_UNKNOWN,"delivery_ambiguous");return
        await self._transition(publication_id,PublicationStatus.SENDING,PublicationStatus.SENT,
                               message_id=message_id)

    async def _transition(self,publication_id,expected,target,error=None,retry_at=None,message_id=None):
        activation_created=False;cancellation_created=False
        async with self.transactions() as tx:
            values={"status":target,"last_error_category":error,"retry_at":retry_at,"updated_at":self.clock()}
            if target is PublicationStatus.SENT:values.update(telegram_message_id=message_id,sent_at=self.clock())
            changed=(await tx._session.execute(update(LimitPendingPublicationRecord).where(
                LimitPendingPublicationRecord.id==publication_id,
                LimitPendingPublicationRecord.status==expected).values(**values))).rowcount
            if changed and target is PublicationStatus.SENT:
                s=tx._session;pending=await s.get(LimitPendingPublicationRecord,publication_id)
                signal=await s.get(SignalRecord,pending.signal_id)
                if signal and signal.status is SignalLifecycleStatus.CANCELLED:
                    result=await s.execute(sqlite_insert(LimitCancellationPublicationRecord).values(
                        signal_id=signal.id,destination_id=pending.destination_id,
                        pending_publication_id=pending.id,reply_to_message_id=message_id,
                        status=PublicationStatus.PENDING,attempt_count=0,
                        created_at=self.clock(),updated_at=self.clock()).on_conflict_do_nothing(
                            index_elements=("signal_id","destination_id")))
                    cancellation_created=result.rowcount==1
                elif signal and signal.status is SignalLifecycleStatus.ACTIVE:
                    activation_created = await tx.publications.queue_limit_initials(
                        signal, self.clock(), pending_id=pending.id)
            await tx.commit()
        if activation_created and self.activation_wake:self.activation_wake()
        if cancellation_created and self.cancellation_wake:self.cancellation_wake()

    async def _definite_no_send(self,publication_id,error,expected=PublicationStatus.RENDERING):
        async with self.transactions() as tx:
            s=tx._session;publication=await s.get(LimitPendingPublicationRecord,publication_id)
            if publication is None or publication.status is not expected:await tx.commit();return
            publication.status=PublicationStatus.FAILED;publication.last_error_category=error
            publication.updated_at=self.clock();await self._release_admission(s,publication);await tx.commit()

    async def _release_slot(self,s,publication_id):
        await s.execute(delete(InitialPublicationDailySlotRecord).where(
            InitialPublicationDailySlotRecord.limit_publication_id==publication_id))

    async def _release_admission(self,s,publication):
        await self._release_slot(s,publication.id)
        signal=await s.get(SignalRecord,publication.signal_id)
        if signal:
            await s.execute(delete(DestinationTradeLockRecord).where(
                DestinationTradeLockRecord.signal_id==signal.id,
                DestinationTradeLockRecord.destination_id==publication.destination_id,
                DestinationTradeLockRecord.canonical_symbol==signal.canonical_symbol))

class LimitCancellationPublicationService:
    def __init__(self,transactions,publisher,message_templates,clock=lambda:datetime.now(timezone.utc),retry_base_seconds=.5):
        self.transactions=transactions;self.publisher=publisher;self.templates=message_templates
        self.clock=clock;self.retry_base=retry_base_seconds
    async def recover(self):
        async with self.transactions() as tx:
            await tx._session.execute(update(LimitCancellationPublicationRecord).where(
                LimitCancellationPublicationRecord.status==PublicationStatus.SENDING).values(
                status=PublicationStatus.DELIVERY_UNKNOWN,last_error_category="restart_after_send_started",updated_at=self.clock()));await tx.commit()
    async def pending_ids(self):
        now=self.clock()
        async with self.transactions() as tx:
            result=tuple((await tx._session.scalars(select(LimitCancellationPublicationRecord.id).where(or_(
                LimitCancellationPublicationRecord.status==PublicationStatus.PENDING,
                (LimitCancellationPublicationRecord.status==PublicationStatus.RETRY_WAIT)&
                (LimitCancellationPublicationRecord.retry_at<=now))))).all());await tx.commit();return result
    async def process(self,item_id):
        now=self.clock()
        async with self.transactions() as tx:
            s=tx._session
            row=(await s.execute(update(LimitCancellationPublicationRecord).where(
                LimitCancellationPublicationRecord.id==item_id,
                LimitCancellationPublicationRecord.status.in_(RETRYABLE)).values(
                status=PublicationStatus.SENDING,attempt_count=LimitCancellationPublicationRecord.attempt_count+1,
                retry_at=None,updated_at=now).returning(LimitCancellationPublicationRecord.signal_id,
                LimitCancellationPublicationRecord.destination_id,LimitCancellationPublicationRecord.reply_to_message_id,
                LimitCancellationPublicationRecord.attempt_count))).one_or_none()
            if row is None:await tx.commit();return
            signal_id,destination_id,parent,attempts=row;signal=await tx.signals.get_by_id(signal_id)
            destination=await s.get(DestinationRecord,destination_id);await tx.commit()
        try:
            text=(await self.templates.resolve(DestinationMessageKind.LIMIT_CANCELLED,
                destination_id=destination_id,context=limit_cancellation_context(signal,
                private=destination.access_level is DestinationAccessLevel.PRIVATE))).text
            message_id=await self.publisher.send_text_update(destination.telegram_chat_id,text,parent)
        except DeliveryRateLimited as exc:
            await self._finish(item_id,PublicationStatus.RETRY_WAIT,"rate_limited",
                self.clock()+timedelta(seconds=max(exc.retry_after,self.retry_base*(2**max(0,attempts-1)))));return
        except DeliveryRejected as exc:
            await self._finish(item_id,PublicationStatus.FAILED,_safe_error(exc));return
        except Exception:
            await self._finish(item_id,PublicationStatus.DELIVERY_UNKNOWN,"delivery_ambiguous");return
        await self._finish(item_id,PublicationStatus.SENT,message_id=message_id)
    async def _finish(self,item_id,target,error=None,retry_at=None,message_id=None):
        values={"status":target,"last_error_category":error,"retry_at":retry_at,"updated_at":self.clock()}
        if target is PublicationStatus.SENT:values.update(telegram_message_id=message_id,sent_at=self.clock())
        async with self.transactions() as tx:
            await tx._session.execute(update(LimitCancellationPublicationRecord).where(
                LimitCancellationPublicationRecord.id==item_id,
                LimitCancellationPublicationRecord.status==PublicationStatus.SENDING).values(**values));await tx.commit()

class LimitPublicationWorker:
    def __init__(self,service,concurrency=3,sweep_seconds=5):
        self.service=service;self.concurrency=concurrency;self.sweep=sweep_seconds
        self._wake=asyncio.Event();self._stop=asyncio.Event();self.task=None
    def wake(self):self._wake.set()
    async def start(self):
        if self.task is None:self.task=asyncio.create_task(self._run())
    async def _run(self):
        while not self._stop.is_set():
            try:
                if hasattr(self.service,"plan_pending"):await self.service.plan_pending()
                ids=await self.service.pending_ids()
                for offset in range(0,len(ids),self.concurrency):
                    results=await asyncio.gather(*(self.service.process(i) for i in ids[offset:offset+self.concurrency]),return_exceptions=True)
                    for item,result in zip(ids[offset:offset+self.concurrency],results):
                        if isinstance(result,BaseException):LOGGER.warning("Limit publication isolated id=%s category=%s",item,type(result).__name__)
            except Exception as exc:LOGGER.warning("Limit publication sweep failed category=%s",type(exc).__name__)
            self._wake.clear()
            try:await asyncio.wait_for(self._wake.wait(),self.sweep)
            except asyncio.TimeoutError:pass
    async def close(self):
        self._stop.set();self._wake.set()
        if self.task:await self.task
