from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.persistence.error_translation import persistence_error
from chainura.persistence.mapping import to_domain
from chainura.persistence.models import SignalistRecord


class SqlAlchemySignalistRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, signalist: Signalist) -> Signalist:
        record = SignalistRecord(
            telegram_user_id=signalist.telegram_user_id,
            display_name=signalist.display_name,
            telegram_username=signalist.telegram_username,
            status=SignalistStatus.ACTIVE,
        )
        self._session.add(record)
        try:
            await self._session.flush()
            await self._session.refresh(record)
        except (IntegrityError, SQLAlchemyError) as exc:
            raise persistence_error(exc) from exc
        return to_domain(record)

    async def get_by_id(self, signalist_id: int) -> Signalist | None:
        try:
            record = await self._session.get(SignalistRecord, signalist_id)
        except SQLAlchemyError as exc:
            raise persistence_error(exc) from exc
        return to_domain(record) if record else None

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Signalist | None:
        try:
            result = await self._session.execute(
                select(SignalistRecord).where(SignalistRecord.telegram_user_id == telegram_user_id)
            )
        except SQLAlchemyError as exc:
            raise persistence_error(exc) from exc
        record = result.scalar_one_or_none()
        return to_domain(record) if record else None

    async def list(self, status: SignalistStatus | None = None) -> list[Signalist]:
        statement = select(SignalistRecord).order_by(SignalistRecord.id)
        if status is not None:
            statement = statement.where(SignalistRecord.status == status)
        try:
            result = await self._session.scalars(statement)
        except SQLAlchemyError as exc:
            raise persistence_error(exc) from exc
        return [to_domain(row) for row in result]

    async def compare_and_swap_status(
        self, signalist_id: int, expected: SignalistStatus, target: SignalistStatus
    ) -> Signalist | None:
        now = datetime.now(timezone.utc)
        values = {
            "status": target,
            "updated_at": now,
            "archived_at": now if target is SignalistStatus.ARCHIVED else None,
        }
        try:
            result = await self._session.execute(
                update(SignalistRecord)
                .where(
                    SignalistRecord.id == signalist_id,
                    SignalistRecord.status == expected,
                )
                .values(**values)
                .returning(SignalistRecord)
            )
            record = result.scalar_one_or_none()
        except (IntegrityError, SQLAlchemyError) as exc:
            raise persistence_error(exc) from exc
        return to_domain(record) if record is not None else None
