from __future__ import annotations
from chainura.errors import TemplateUploadTransportError
from chainura.rendering.templates import MAX_TEMPLATE_BYTES

async def download_png_document(bot, document, *, max_bytes=MAX_TEMPLATE_BYTES):
 """Download an admin document without trusting its name or Telegram's size claim."""
 if document is None or getattr(document,"file_id",None) in (None,""):
  raise TemplateUploadTransportError("PNG document metadata is missing")
 if getattr(document,"mime_type",None)!="image/png":
  raise TemplateUploadTransportError("the upload must be a PNG document")
 declared=getattr(document,"file_size",None)
 if declared is not None and (isinstance(declared,bool) or not isinstance(declared,int) or declared<1 or declared>max_bytes):
  raise TemplateUploadTransportError("PNG document size is invalid")
 try:
  telegram_file=await bot.get_file(document.file_id)
  if not getattr(telegram_file,"file_path",None):raise TemplateUploadTransportError("Telegram file metadata is missing")
  stream=await bot.download_file(telegram_file.file_path)
  data=stream.read(max_bytes+1)
 except TemplateUploadTransportError:raise
 except Exception as exc:raise TemplateUploadTransportError("PNG document download failed") from exc
 if not isinstance(data,bytes) or not data or len(data)>max_bytes:
  raise TemplateUploadTransportError("PNG document exceeds the upload limit")
 return data

class TemplateAdministrationService:
 def __init__(self,templates,wake):self.templates=templates;self.wake=wake
 async def install(self,exchange,data,*,destination_id=None):
  asset=await self.templates.install(exchange,data,destination_id=destination_id);self.wake();return asset
 async def remove_override(self,exchange,destination_id):
  await self.templates.remove_override(exchange,destination_id);self.wake()
