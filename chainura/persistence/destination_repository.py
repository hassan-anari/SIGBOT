from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from chainura.domain.destination import (Destination, DestinationExchangePriority,
    DestinationSignalistAssignment, DestinationStatus, Exchange)
from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.errors import (DestinationPersistenceError, DuplicateDestinationAssignmentError,
    DuplicateDestinationChatIdError, DuplicateDestinationInternalNameError)
from chainura.persistence.mapping import to_domain as signalist_domain
from chainura.persistence.error_translation import destination_persistence_error
from chainura.persistence.models import (DestinationExchangePriorityRecord, DestinationRecord,
    DestinationSignalistRecord, SignalistRecord)

def destination_domain(r: DestinationRecord) -> Destination:
    return Destination.rehydrate(id=r.id, internal_name=r.internal_name,
        telegram_chat_id=r.telegram_chat_id, destination_type=r.destination_type,
        access_level=r.access_level, status=r.status, daily_limit=r.daily_limit,
        created_at=r.created_at, updated_at=r.updated_at)

def _error(exc: Exception) -> Exception:
    return destination_persistence_error(exc)

class SqlAlchemyDestinationRepository:
    def __init__(self, session: AsyncSession): self._session = session
    async def add(self, item: Destination) -> Destination:
        record = DestinationRecord(internal_name=item.internal_name, telegram_chat_id=item.telegram_chat_id,
            destination_type=item.destination_type, access_level=item.access_level,
            status=item.status, daily_limit=item.daily_limit)
        self._session.add(record)
        try: await self._session.flush(); await self._session.refresh(record)
        except (IntegrityError, SQLAlchemyError) as exc: raise _error(exc) from exc
        return destination_domain(record)
    async def get_by_id(self, id: int, fresh: bool=False) -> Destination | None:
        try: record = await self._session.get(DestinationRecord, id, populate_existing=fresh)
        except SQLAlchemyError as exc: raise _error(exc) from exc
        return destination_domain(record) if record else None
    async def get_by_chat_id(self, value: int) -> Destination | None:
        return await self._one(DestinationRecord.telegram_chat_id == value)
    async def get_by_internal_name(self, value: str) -> Destination | None:
        return await self._one(DestinationRecord.internal_name == value)
    async def _one(self, condition) -> Destination | None:
        try: record=(await self._session.scalars(select(DestinationRecord).where(condition))).one_or_none()
        except SQLAlchemyError as exc: raise _error(exc) from exc
        return destination_domain(record) if record else None
    async def count(self, status=None) -> int:
        q=select(func.count()).select_from(DestinationRecord)
        if status is not None: q=q.where(DestinationRecord.status == status)
        try: return int(await self._session.scalar(q) or 0)
        except SQLAlchemyError as exc: raise _error(exc) from exc
    async def list_page(self, status, offset: int, limit: int) -> list[Destination]:
        q=select(DestinationRecord).order_by(DestinationRecord.id.desc()).offset(offset).limit(limit)
        if status is not None: q=q.where(DestinationRecord.status == status)
        try: rows=await self._session.scalars(q)
        except SQLAlchemyError as exc: raise _error(exc) from exc
        return [destination_domain(x) for x in rows]
    async def update_metadata(self, current: Destination, candidate: Destination) -> Destination | None:
        now=datetime.now(timezone.utc)
        try:
            result=await self._session.execute(update(DestinationRecord).where(
                DestinationRecord.id==current.id, DestinationRecord.updated_at==current.updated_at).values(
                internal_name=candidate.internal_name, destination_type=candidate.destination_type,
                access_level=candidate.access_level, daily_limit=candidate.daily_limit, updated_at=now))
            await self._session.flush()
        except (IntegrityError, SQLAlchemyError) as exc: raise _error(exc) from exc
        return await self.get_by_id(current.id, True) if result.rowcount == 1 else None
    async def compare_and_swap_status(self, id: int, expected: DestinationStatus, target: DestinationStatus):
        try:
            result=await self._session.execute(update(DestinationRecord).where(
                DestinationRecord.id==id, DestinationRecord.status==expected).values(
                status=target, updated_at=datetime.now(timezone.utc)))
        except SQLAlchemyError as exc: raise _error(exc) from exc
        if result.rowcount not in {0,1}: raise DestinationPersistenceError("unexpected affected rows")
        return await self.get_by_id(id, True) if result.rowcount else None
    async def touch(self, id: int, expected_updated_at: datetime) -> bool:
        try:
            result=await self._session.execute(update(DestinationRecord).where(
                DestinationRecord.id==id, DestinationRecord.updated_at==expected_updated_at).values(
                updated_at=datetime.now(timezone.utc)))
        except SQLAlchemyError as exc: raise _error(exc) from exc
        if result.rowcount not in {0,1}: raise DestinationPersistenceError("unexpected affected rows")
        return result.rowcount == 1
    async def get_priority(self, id: int) -> DestinationExchangePriority:
        try:
            rows=await self._session.scalars(select(DestinationExchangePriorityRecord).where(
                DestinationExchangePriorityRecord.destination_id==id).order_by(DestinationExchangePriorityRecord.position))
        except SQLAlchemyError as exc: raise _error(exc) from exc
        return DestinationExchangePriority(tuple(row.exchange for row in rows))
    async def replace_priority(self, id: int, priority: DestinationExchangePriority) -> None:
        try:
            await self._session.execute(delete(DestinationExchangePriorityRecord).where(
                DestinationExchangePriorityRecord.destination_id==id))
            self._session.add_all([DestinationExchangePriorityRecord(destination_id=id, exchange=e, position=i)
                                   for i,e in enumerate(priority.exchanges, 1)])
            await self._session.flush()
        except (IntegrityError, SQLAlchemyError) as exc: raise _error(exc) from exc
    async def assignment_exists(self, did: int, sid: int) -> bool:
        try: return await self._session.get(DestinationSignalistRecord, (did,sid)) is not None
        except SQLAlchemyError as exc: raise _error(exc) from exc
    async def add_assignment(self, did: int, sid: int) -> DestinationSignalistAssignment:
        row=DestinationSignalistRecord(destination_id=did, signalist_id=sid); self._session.add(row)
        try: await self._session.flush(); await self._session.refresh(row)
        except (IntegrityError, SQLAlchemyError) as exc: raise _error(exc) from exc
        return DestinationSignalistAssignment(did,sid,row.created_at)
    async def remove_assignment(self, did: int, sid: int) -> bool:
        try:
            result=await self._session.execute(delete(DestinationSignalistRecord).where(
                DestinationSignalistRecord.destination_id==did, DestinationSignalistRecord.signalist_id==sid))
        except SQLAlchemyError as exc: raise _error(exc) from exc
        if result.rowcount not in {0,1}: raise DestinationPersistenceError("unexpected affected rows")
        return result.rowcount == 1
    async def count_assigned(self, did: int) -> int:
        try:
            return int(await self._session.scalar(select(func.count()).select_from(DestinationSignalistRecord).where(
                DestinationSignalistRecord.destination_id==did)) or 0)
        except SQLAlchemyError as exc: raise _error(exc) from exc
    async def list_assigned(self, did: int, offset: int, limit: int) -> list[Signalist]:
        q=select(SignalistRecord).join(DestinationSignalistRecord,
            DestinationSignalistRecord.signalist_id==SignalistRecord.id).where(
            DestinationSignalistRecord.destination_id==did).order_by(SignalistRecord.id.desc()).offset(offset).limit(limit)
        try: rows=await self._session.scalars(q)
        except SQLAlchemyError as exc: raise _error(exc) from exc
        return [signalist_domain(x) for x in rows]
    def _eligible(self, did: int):
        assigned=select(DestinationSignalistRecord.signalist_id).where(DestinationSignalistRecord.destination_id==did)
        return (SignalistRecord.status.in_([SignalistStatus.ACTIVE,SignalistStatus.INACTIVE]), ~SignalistRecord.id.in_(assigned))
    async def count_eligible(self,did:int)->int:
        try: return int(await self._session.scalar(select(func.count()).select_from(SignalistRecord).where(*self._eligible(did))) or 0)
        except SQLAlchemyError as exc: raise _error(exc) from exc
    async def list_eligible(self,did:int,offset:int,limit:int)->list[Signalist]:
        q=select(SignalistRecord).where(*self._eligible(did)).order_by(SignalistRecord.id.desc()).offset(offset).limit(limit)
        try: rows=await self._session.scalars(q)
        except SQLAlchemyError as exc: raise _error(exc) from exc
        return [signalist_domain(x) for x in rows]
    async def count_for_signalist(self,sid:int)->int:
        try: return int(await self._session.scalar(select(func.count()).select_from(DestinationSignalistRecord).where(DestinationSignalistRecord.signalist_id==sid)) or 0)
        except SQLAlchemyError as exc: raise _error(exc) from exc
    async def list_for_signalist(self,sid:int,offset:int,limit:int)->list[Destination]:
        q=select(DestinationRecord).join(DestinationSignalistRecord).where(DestinationSignalistRecord.signalist_id==sid).order_by(DestinationRecord.id.desc()).offset(offset).limit(limit)
        try: rows=await self._session.scalars(q)
        except SQLAlchemyError as exc: raise _error(exc) from exc
        return [destination_domain(x) for x in rows]
