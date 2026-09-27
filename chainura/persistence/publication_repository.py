from __future__ import annotations

from datetime import datetime
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from chainura.domain.destination import DestinationAccessLevel, DestinationStatus
from chainura.domain.publication import (PublicationDelivery, PublicationRequestStatus,
    PublicationStatus, TERMINAL_PUBLICATION_STATUSES, select_initial_exchange)
from chainura.errors import DailyCapacityError, PublicationPersistenceError
from chainura.persistence.models import (DestinationExchangePriorityRecord, DestinationPublicationRecord,
    DestinationRecord, DestinationSignalistRecord, InitialPublicationDailySlotRecord,
    InitialPublicationRequestRecord, InitialPublicationNotificationRecord,
    SignalInitialPriceRecord, SignalRecord, SignalMonitorStateRecord,
    DestinationTradeLockRecord, utc_now)
from chainura.domain.signal import SignalLifecycleStatus
from chainura.domain.monitoring import MonitorStage

TRADE_LOCK_REASON = "destination_trade_lock_occupied"
VISIBLE_INITIAL_STATUSES = (PublicationStatus.SENT, PublicationStatus.DELIVERY_UNKNOWN,
                            PublicationStatus.SENDING)
TERMINAL_MONITOR_STAGES = (MonitorStage.COMPLETED_TARGETS, MonitorStage.COMPLETED_SL,
                           MonitorStage.COMPLETED_BREAKEVEN,
                           MonitorStage.COMPLETED_FULL_CLOSE,
                           MonitorStage.COMPLETED_RISK_FREE)


class SqlAlchemyPublicationRepository:
    def __init__(self, session): self.session = session

    async def acquire_trade_lock(self, signal_id: int, destination_id: int,
                                 canonical_symbol: str, now: datetime) -> bool:
        """Atomically admit a slot, while conservatively honoring visible legacy rows."""
        legacy = await self.session.scalar(select(DestinationPublicationRecord.id).join(
            SignalRecord, SignalRecord.id == DestinationPublicationRecord.signal_id).outerjoin(
            SignalMonitorStateRecord, SignalMonitorStateRecord.signal_id == SignalRecord.id).where(
            DestinationPublicationRecord.destination_id == destination_id,
            DestinationPublicationRecord.signal_id != signal_id,
            DestinationPublicationRecord.status.in_(VISIBLE_INITIAL_STATUSES),
            DestinationPublicationRecord.signal_id == SignalRecord.id,
            SignalRecord.canonical_symbol == canonical_symbol,
            SignalRecord.status != SignalLifecycleStatus.CANCELLED,
            SignalMonitorStateRecord.signal_id.is_(None) |
            SignalMonitorStateRecord.stage.not_in(TERMINAL_MONITOR_STAGES),
        ).limit(1))
        if legacy is not None:
            return False
        statement = sqlite_insert(DestinationTradeLockRecord).values(
            destination_id=destination_id, canonical_symbol=canonical_symbol,
            signal_id=signal_id, acquired_at=now, updated_at=now).on_conflict_do_nothing(
            index_elements=["destination_id", "canonical_symbol"])
        await self.session.execute(statement)
        owner = await self.session.scalar(select(DestinationTradeLockRecord.signal_id).where(
            DestinationTradeLockRecord.destination_id == destination_id,
            DestinationTradeLockRecord.canonical_symbol == canonical_symbol))
        return owner == signal_id

    async def release_trade_lock(self, signal_id: int, destination_id: int | None = None):
        condition = DestinationTradeLockRecord.signal_id == signal_id
        if destination_id is not None:
            condition &= DestinationTradeLockRecord.destination_id == destination_id
        await self.session.execute(delete(DestinationTradeLockRecord).where(condition))

    async def recover(self, now: datetime):
        try:
            await self.session.execute(update(InitialPublicationRequestRecord).where(
                InitialPublicationRequestRecord.status == PublicationRequestStatus.PLANNING).values(
                status=PublicationRequestStatus.PENDING, updated_at=now))
            await self.session.execute(update(DestinationPublicationRecord).where(
                DestinationPublicationRecord.status.in_([PublicationStatus.RENDERING, PublicationStatus.READY])).values(
                status=PublicationStatus.PENDING, updated_at=now))
            await self.session.execute(update(DestinationPublicationRecord).where(
                DestinationPublicationRecord.status == PublicationStatus.SENDING).values(
                status=PublicationStatus.DELIVERY_UNKNOWN, last_error_category="restart_after_send_started", updated_at=now))
            await self.session.execute(update(DestinationPublicationRecord).where(
                DestinationPublicationRecord.status == PublicationStatus.RETRY_WAIT,
                DestinationPublicationRecord.retry_at <= now).values(
                status=PublicationStatus.PENDING, retry_at=None, updated_at=now))
            await self.reconcile_trade_locks(now)
            signal_ids=tuple((await self.session.scalars(select(InitialPublicationRequestRecord.signal_id).where(
                InitialPublicationRequestRecord.status.in_([PublicationRequestStatus.PLANNED,
                    PublicationRequestStatus.PLANNING])))).all())
            for signal_id in signal_ids:await self.complete_request(signal_id,now)
        except SQLAlchemyError as exc: raise PublicationPersistenceError("publication recovery failed") from exc

    async def reconcile_trade_locks(self, now: datetime):
        """Deterministically admit legacy queued Initials before new work is planned."""
        queued = (PublicationStatus.PENDING, PublicationStatus.RETRY_WAIT,
                  PublicationStatus.BLOCKED_TEMPLATE)
        rows = (await self.session.execute(select(
            DestinationPublicationRecord, SignalRecord, SignalMonitorStateRecord).join(
            SignalRecord, SignalRecord.id == DestinationPublicationRecord.signal_id).outerjoin(
            SignalMonitorStateRecord,
            SignalMonitorStateRecord.signal_id == SignalRecord.id).where(
            DestinationPublicationRecord.status.in_(queued)).order_by(
            DestinationPublicationRecord.created_at,
            DestinationPublicationRecord.id))).all()
        for publication, signal, monitor in rows:
            terminal = (signal.status is SignalLifecycleStatus.CANCELLED or
                        (monitor is not None and monitor.stage in TERMINAL_MONITOR_STAGES))
            if terminal:
                publication.status = PublicationStatus.SUPERSEDED
                publication.updated_at = now
                await self.release_capacity(publication.id)
                await self.release_trade_lock(signal.id, publication.destination_id)
                continue
            admitted = await self.acquire_trade_lock(signal.id, publication.destination_id,
                                                     signal.canonical_symbol, now)
            if not admitted:
                publication.status = PublicationStatus.SUPERSEDED
                publication.last_error_category = TRADE_LOCK_REASON
                publication.updated_at = now
                await self.release_capacity(publication.id)

    async def pending_request_ids(self, limit=50):
        try:return tuple((await self.session.scalars(select(InitialPublicationRequestRecord.id).where(
            InitialPublicationRequestRecord.status == PublicationRequestStatus.PENDING).order_by(
            InitialPublicationRequestRecord.created_at).limit(limit))).all())
        except SQLAlchemyError as exc:raise PublicationPersistenceError("publication request read failed") from exc

    async def plan(self, request_id: int, now: datetime):
        claimed=(await self.session.execute(update(InitialPublicationRequestRecord).where(
            InitialPublicationRequestRecord.id==request_id,
            InitialPublicationRequestRecord.status==PublicationRequestStatus.PENDING).values(
            status=PublicationRequestStatus.PLANNING,updated_at=now).returning(InitialPublicationRequestRecord.signal_id))).scalar_one_or_none()
        if claimed is None:return False
        signal=await self.session.get(SignalRecord,claimed)
        if signal is None or signal.status is SignalLifecycleStatus.CANCELLED:
            await self.session.execute(update(InitialPublicationRequestRecord).where(InitialPublicationRequestRecord.id==request_id).values(status=PublicationRequestStatus.SUPERSEDED,updated_at=now));return True
        sources=frozenset((await self.session.scalars(select(SignalInitialPriceRecord.exchange).where(
            SignalInitialPriceRecord.signal_id==claimed))).all())
        rows=(await self.session.execute(select(DestinationRecord.id, DestinationExchangePriorityRecord.exchange).join(
            DestinationSignalistRecord,DestinationSignalistRecord.destination_id==DestinationRecord.id).join(
            DestinationExchangePriorityRecord,DestinationExchangePriorityRecord.destination_id==DestinationRecord.id).where(
            DestinationSignalistRecord.signalist_id==signal.signalist_id,
            DestinationRecord.status==DestinationStatus.ACTIVE).order_by(DestinationRecord.id,DestinationExchangePriorityRecord.position))).all()
        grouped={}
        for destination_id,exchange in rows:grouped.setdefault(destination_id,[]).append(exchange)
        for destination_id,priorities in grouped.items():
            selected=select_initial_exchange(signal.mode,signal.selected_exchange,priorities,sources)
            if selected is not None:
                admitted=await self.acquire_trade_lock(claimed,destination_id,signal.canonical_symbol,now)
                self.session.add(DestinationPublicationRecord(
                    signal_id=claimed,destination_id=destination_id,event_kind="initial",selected_exchange=selected,
                    status=PublicationStatus.PENDING if admitted else PublicationStatus.SUPERSEDED,
                    last_error_category=None if admitted else TRADE_LOCK_REASON,created_at=now,updated_at=now))
        await self.session.flush()
        await self.session.execute(update(InitialPublicationRequestRecord).where(
            InitialPublicationRequestRecord.id==request_id).values(status=PublicationRequestStatus.PLANNED,
            planned_at=now,updated_at=now))
        await self.complete_request(claimed,now)
        return True

    async def pending_delivery_ids(self, now, limit=100):
        return tuple((await self.session.scalars(select(DestinationPublicationRecord.id).where(
            ((DestinationPublicationRecord.status == PublicationStatus.PENDING) |
             ((DestinationPublicationRecord.status == PublicationStatus.RETRY_WAIT) &
              (DestinationPublicationRecord.retry_at <= now)))).order_by(
            DestinationPublicationRecord.created_at).limit(limit))).all())

    async def claim(self, publication_id, now):
        available=((DestinationPublicationRecord.status == PublicationStatus.PENDING) |
            ((DestinationPublicationRecord.status == PublicationStatus.RETRY_WAIT) &
             (DestinationPublicationRecord.retry_at <= now)))
        claimed=(await self.session.execute(update(DestinationPublicationRecord).where(
            DestinationPublicationRecord.id==publication_id,available).values(
            status=PublicationStatus.RENDERING,updated_at=now,
            retry_at=None,
            first_attempt_at=func.coalesce(DestinationPublicationRecord.first_attempt_at,now)).returning(
            DestinationPublicationRecord.signal_id,DestinationPublicationRecord.destination_id,
            DestinationPublicationRecord.selected_exchange,DestinationPublicationRecord.attempt_count))).one_or_none()
        if claimed is None:return None
        signal_id,destination_id,exchange,attempts=claimed
        destination=await self.session.get(DestinationRecord,destination_id)
        signalist_id=await self.session.scalar(select(SignalRecord.signalist_id).where(SignalRecord.id==signal_id))
        assigned=await self.session.scalar(select(exists().where(DestinationSignalistRecord.destination_id==destination_id,
            DestinationSignalistRecord.signalist_id==signalist_id)))
        if destination is None:return None
        return PublicationDelivery(publication_id,signal_id,destination_id,exchange,PublicationStatus.RENDERING,
            attempts,destination.telegram_chat_id,destination.access_level is DestinationAccessLevel.PRIVATE,destination.daily_limit),destination.status,assigned

    async def transition(self, publication_id, expected, target, now, *, error=None, message_id=None, template_sha=None, increment=False,retry_at=None):
        values={"status":target,"updated_at":now,"last_error_category":error,
                "template_sha256":template_sha,"retry_at":retry_at}
        if increment:values["attempt_count"]=DestinationPublicationRecord.attempt_count+1
        if target is PublicationStatus.SENT:values.update(telegram_message_id=message_id,sent_at=now)
        result=await self.session.execute(update(DestinationPublicationRecord).where(
            DestinationPublicationRecord.id==publication_id,DestinationPublicationRecord.status==expected).values(**values))
        return result.rowcount==1

    async def prepare_send(self,publication_id,signal_id,destination_id,now):
        destination=await self.session.get(DestinationRecord,destination_id)
        signal=await self.session.get(SignalRecord,signal_id);signalist_id=signal.signalist_id if signal else None
        assigned=bool(await self.session.scalar(select(exists().where(
            DestinationSignalistRecord.destination_id==destination_id,
            DestinationSignalistRecord.signalist_id==signalist_id))))
        if signal is None or signal.status is SignalLifecycleStatus.CANCELLED:
            target=PublicationStatus.SUPERSEDED
        elif destination is None or destination.status is not DestinationStatus.ACTIVE:
            target=PublicationStatus.SKIPPED_INACTIVE
        elif not assigned:target=PublicationStatus.SKIPPED_UNASSIGNED
        else:
            admitted=await self.acquire_trade_lock(signal_id,destination_id,signal.canonical_symbol,now)
            if not admitted:
                changed=await self.transition(publication_id,PublicationStatus.RENDERING,
                    PublicationStatus.SUPERSEDED,now,error=TRADE_LOCK_REASON)
                if changed:await self.release_capacity(publication_id)
                return PublicationStatus.SUPERSEDED.value if changed else "lost"
            changed=await self.transition(publication_id,PublicationStatus.RENDERING,
                PublicationStatus.SENDING,now,increment=True)
            return "sending" if changed else "lost"
        changed=await self.transition(publication_id,PublicationStatus.RENDERING,target,now)
        if changed:
            await self.release_capacity(publication_id)
            await self.release_trade_lock(signal_id,destination_id)
        return target.value if changed else "lost"

    async def claim_capacity(self, publication_id,destination_id,local_date,limit,now):
        existing=await self.session.scalar(select(InitialPublicationDailySlotRecord).where(
            InitialPublicationDailySlotRecord.publication_id==publication_id))
        if existing:return existing.slot_number
        used=set((await self.session.scalars(select(InitialPublicationDailySlotRecord.slot_number).where(
            InitialPublicationDailySlotRecord.destination_id==destination_id,
            InitialPublicationDailySlotRecord.local_date==local_date))).all())
        slot=next((n for n in range(1,limit+1) if n not in used),None)
        if slot is None:return None
        self.session.add(InitialPublicationDailySlotRecord(destination_id=destination_id,local_date=local_date,
            slot_number=slot,publication_id=publication_id,created_at=now))
        try:await self.session.flush()
        except IntegrityError as exc:raise DailyCapacityError("daily capacity conflict") from exc
        return slot

    async def release_capacity(self,publication_id):
        await self.session.execute(delete(InitialPublicationDailySlotRecord).where(
            InitialPublicationDailySlotRecord.publication_id==publication_id))

    async def complete_request(self,signal_id,now):
        unfinished=await self.session.scalar(select(func.count()).select_from(DestinationPublicationRecord).where(
            DestinationPublicationRecord.signal_id==signal_id,
            DestinationPublicationRecord.status.not_in(tuple(TERMINAL_PUBLICATION_STATUSES))))
        if not unfinished:
            changed=(await self.session.execute(update(InitialPublicationRequestRecord).where(
                InitialPublicationRequestRecord.signal_id==signal_id,
                InitialPublicationRequestRecord.status.in_([PublicationRequestStatus.PLANNED,PublicationRequestStatus.PLANNING])).values(
                status=PublicationRequestStatus.COMPLETED,completed_at=now,updated_at=now))).rowcount
            if changed:
                results=tuple((await self.session.execute(select(
                    DestinationPublicationRecord.status,DestinationPublicationRecord.last_error_category).where(
                    DestinationPublicationRecord.signal_id==signal_id))).all())
                visible=tuple(status for status,reason in results if reason != TRADE_LOCK_REASON)
                hidden_only=bool(results) and not visible
                outcome="success" if hidden_only or (visible and all(x is PublicationStatus.SENT for x in visible)) else "partial"
                self.session.add(InitialPublicationNotificationRecord(signal_id=signal_id,status="pending",outcome=outcome,created_at=now,updated_at=now))

    async def last_published_message_id(self,signal_id,destination_id):
        return await self.session.scalar(select(DestinationPublicationRecord.telegram_message_id).where(
            DestinationPublicationRecord.signal_id==signal_id,DestinationPublicationRecord.destination_id==destination_id,
            DestinationPublicationRecord.status==PublicationStatus.SENT).order_by(DestinationPublicationRecord.sent_at.desc()).limit(1))

    async def unblock_templates(self,now):
        await self.session.execute(update(DestinationPublicationRecord).where(
            DestinationPublicationRecord.status==PublicationStatus.BLOCKED_TEMPLATE).values(
            status=PublicationStatus.PENDING,last_error_category=None,updated_at=now))
