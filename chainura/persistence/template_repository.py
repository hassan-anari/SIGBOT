from sqlalchemy import delete,select
from sqlalchemy.exc import SQLAlchemyError
from chainura.persistence.models import SignalTemplateRecord,utc_now
from chainura.errors import TemplatePersistenceError

class SqlAlchemyTemplateRepository:
 def __init__(self,session): self.session=session
 async def get(self,exchange,destination_id=None):
  try:return await self.session.scalar(select(SignalTemplateRecord).where(SignalTemplateRecord.exchange==exchange,SignalTemplateRecord.destination_id.is_(None) if destination_id is None else SignalTemplateRecord.destination_id==destination_id))
  except SQLAlchemyError as exc: raise TemplatePersistenceError("template metadata read failed") from exc
 async def replace(self,asset,destination_id=None):
  record=await self.get(asset.exchange,destination_id)
  if record is None:
   record=SignalTemplateRecord(exchange=asset.exchange,destination_id=destination_id,storage_key=asset.storage_key,sha256=asset.sha256,width=asset.width,height=asset.height,content_type=asset.content_type)
   self.session.add(record)
  else:
   record.storage_key=asset.storage_key;record.sha256=asset.sha256;record.width=asset.width;record.height=asset.height;record.content_type=asset.content_type;record.updated_at=utc_now()
  return record
 async def remove_override(self,exchange,destination_id):
  try: await self.session.execute(delete(SignalTemplateRecord).where(SignalTemplateRecord.exchange==exchange,SignalTemplateRecord.destination_id==destination_id))
  except SQLAlchemyError as exc: raise TemplatePersistenceError("template metadata removal failed") from exc
