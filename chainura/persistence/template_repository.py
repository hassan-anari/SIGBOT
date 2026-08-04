from dataclasses import dataclass
from sqlalchemy import delete,select
from sqlalchemy.exc import SQLAlchemyError
from chainura.domain.destination import Exchange
from chainura.persistence.models import SignalTemplateRecord,utc_now
from chainura.errors import TemplatePersistenceError

@dataclass(frozen=True,slots=True)
class TemplateMetadata:
    exchange:Exchange;destination_id:int|None;storage_key:str;sha256:str
    width:int;height:int;content_type:str

def _dto(record):
    return None if record is None else TemplateMetadata(record.exchange,record.destination_id,
        record.storage_key,record.sha256,record.width,record.height,record.content_type)

class SqlAlchemyTemplateRepository:
    def __init__(self,session):self.session=session
    async def get(self,exchange,destination_id=None):
        try:
            condition=(SignalTemplateRecord.destination_id.is_(None) if destination_id is None
                       else SignalTemplateRecord.destination_id==destination_id)
            return _dto(await self.session.scalar(select(SignalTemplateRecord).where(
                SignalTemplateRecord.exchange==exchange,condition)))
        except SQLAlchemyError as exc:raise TemplatePersistenceError("template metadata read failed") from exc
    async def replace(self,asset,destination_id=None):
        try:
            condition=(SignalTemplateRecord.destination_id.is_(None) if destination_id is None
                       else SignalTemplateRecord.destination_id==destination_id)
            record=await self.session.scalar(select(SignalTemplateRecord).where(
                SignalTemplateRecord.exchange==asset.exchange,condition))
            if record is None:
                self.session.add(SignalTemplateRecord(exchange=asset.exchange,destination_id=destination_id,
                    storage_key=asset.storage_key,sha256=asset.sha256,width=asset.width,height=asset.height,
                    content_type=asset.content_type))
            else:
                record.storage_key=asset.storage_key;record.sha256=asset.sha256
                record.width=asset.width;record.height=asset.height
                record.content_type=asset.content_type;record.updated_at=utc_now()
            await self.session.flush()
        except SQLAlchemyError as exc:raise TemplatePersistenceError("template metadata update failed") from exc
    async def remove_override(self,exchange,destination_id):
        try:await self.session.execute(delete(SignalTemplateRecord).where(
            SignalTemplateRecord.exchange==exchange,SignalTemplateRecord.destination_id==destination_id))
        except SQLAlchemyError as exc:raise TemplatePersistenceError("template metadata removal failed") from exc
