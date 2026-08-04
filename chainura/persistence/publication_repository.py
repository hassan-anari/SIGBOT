from __future__ import annotations

from datetime import datetime
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from chainura.domain.destination import DestinationAccessLevel, DestinationStatus
from chainura.domain.publication import (PublicationDelivery, PublicationRequestStatus,
    PublicationStatus, TERMINAL_PUBLICATION_STATUSES, select_initial_exchange)
from chainura.errors import DailyCapacityError, PublicationPersistenceError
from chainura.persistence.models import (DestinationExchangePriorityRecord, DestinationPublicationRecord,
    DestinationRecord, DestinationSignalistRecord, InitialPublicationDailySlotRecord,
    InitialPublicationRequestRecord, SignalInitialPriceRecord, SignalRecord, utc_now)


class SqlAlchemyPublicationRepository:
    def __init__(self, session): self.session = session

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
                DestinationPublicationRecord.status == PublicationStatus.RETRY_WAIT).values(
                status=PublicationStatus.PENDING, updated_at=now))
        except SQLAlchemyError as exc: raise PublicationPersistenceError("publication recovery failed") from exc

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
            if selected is not None:self.session.add(DestinationPublicationRecord(
                signal_id=claimed,destination_id=destination_id,event_kind="initial",selected_exchange=selected,
                status=PublicationStatus.PENDING,created_at=now,updated_at=now))
        await self.session.flush()
        await self.session.execute(update(InitialPublicationRequestRecord).where(
            InitialPublicationRequestRecord.id==request_id).values(status=PublicationRequestStatus.PLANNED,
            planned_at=now,updated_at=now))
        await self.complete_request(claimed,now)
        return True

    async def pending_delivery_ids(self, limit=100):
        return tuple((await self.session.scalars(select(DestinationPublicationRecord.id).where(
            DestinationPublicationRecord.status == PublicationStatus.PENDING).order_by(
            DestinationPublicationRecord.created_at).limit(limit))).all())

    async def claim(self, publication_id, now):
        claimed=(await self.session.execute(update(DestinationPublicationRecord).where(
            DestinationPublicationRecord.id==publication_id,
            DestinationPublicationRecord.status.in_([PublicationStatus.PENDING,PublicationStatus.BLOCKED_TEMPLATE])).values(
            status=PublicationStatus.RENDERING,updated_at=now,
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

    async def transition(self, publication_id, expected, target, now, *, error=None, message_id=None, template_sha=None, increment=False):
        values={"status":target,"updated_at":now,"last_error_category":error,
                "template_sha256":template_sha}
        if increment:values["attempt_count"]=DestinationPublicationRecord.attempt_count+1
        if target is PublicationStatus.SENT:values.update(telegram_message_id=message_id,sent_at=now)
        result=await self.session.execute(update(DestinationPublicationRecord).where(
            DestinationPublicationRecord.id==publication_id,DestinationPublicationRecord.status==expected).values(**values))
        return result.rowcount==1

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
        if not unfinished:await self.session.execute(update(InitialPublicationRequestRecord).where(
            InitialPublicationRequestRecord.signal_id==signal_id,
            InitialPublicationRequestRecord.status.in_([PublicationRequestStatus.PLANNED,PublicationRequestStatus.PLANNING])).values(
            status=PublicationRequestStatus.COMPLETED,completed_at=now,updated_at=now))

    async def last_published_message_id(self,signal_id,destination_id):
        return await self.session.scalar(select(DestinationPublicationRecord.telegram_message_id).where(
            DestinationPublicationRecord.signal_id==signal_id,DestinationPublicationRecord.destination_id==destination_id,
            DestinationPublicationRecord.status==PublicationStatus.SENT).order_by(DestinationPublicationRecord.sent_at.desc()).limit(1))

    async def unblock_templates(self,now):
        await self.session.execute(update(DestinationPublicationRecord).where(
            DestinationPublicationRecord.status==PublicationStatus.BLOCKED_TEMPLATE).values(
            status=PublicationStatus.PENDING,last_error_category=None,updated_at=now))
