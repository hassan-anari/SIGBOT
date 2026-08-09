from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from chainura.persistence.models import DestinationMessageTemplateRecord, utc_now
from chainura.errors import TemplatePersistenceError


class SqlAlchemyMessageTemplateRepository:
    def __init__(self, session):
        self.session = session

    async def _get(self, kind, destination_id):
        condition = (DestinationMessageTemplateRecord.destination_id.is_(None)
                     if destination_id is None else DestinationMessageTemplateRecord.destination_id == destination_id)
        try:
            record = await self.session.scalar(select(DestinationMessageTemplateRecord).where(
                DestinationMessageTemplateRecord.message_kind == kind, condition))
            return None if record is None else record.template_text
        except SQLAlchemyError as exc:
            raise TemplatePersistenceError("message template read failed") from exc

    async def get_global(self, kind): return await self._get(kind, None)
    async def get_destination(self, kind, destination_id): return await self._get(kind, destination_id)

    async def _set(self, kind, destination_id, text):
        condition = (DestinationMessageTemplateRecord.destination_id.is_(None)
                     if destination_id is None else DestinationMessageTemplateRecord.destination_id == destination_id)
        try:
            record = await self.session.scalar(select(DestinationMessageTemplateRecord).where(
                DestinationMessageTemplateRecord.message_kind == kind, condition))
            if record is None:
                self.session.add(DestinationMessageTemplateRecord(message_kind=kind, destination_id=destination_id, template_text=text))
            else:
                record.template_text = text
                record.updated_at = utc_now()
            await self.session.flush()
        except SQLAlchemyError as exc:
            raise TemplatePersistenceError("message template update failed") from exc

    async def set_global(self, kind, text): await self._set(kind, None, text)
    async def set_destination(self, kind, destination_id, text): await self._set(kind, destination_id, text)

    async def _remove(self, kind, destination_id):
        condition = (DestinationMessageTemplateRecord.destination_id.is_(None)
                     if destination_id is None else DestinationMessageTemplateRecord.destination_id == destination_id)
        try:
            await self.session.execute(delete(DestinationMessageTemplateRecord).where(
                DestinationMessageTemplateRecord.message_kind == kind, condition))
        except SQLAlchemyError as exc:
            raise TemplatePersistenceError("message template removal failed") from exc

    async def remove_global(self, kind): await self._remove(kind, None)
    async def remove_destination(self, kind, destination_id): await self._remove(kind, destination_id)
