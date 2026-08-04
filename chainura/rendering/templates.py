from __future__ import annotations
import hashlib,io,os,tempfile,warnings
from dataclasses import dataclass
from pathlib import Path
from PIL import Image,UnidentifiedImageError
from chainura.domain.destination import Exchange
from chainura.errors import TemplateAssetError,TemplateNotConfiguredError,TemplatePersistenceError,TemplateValidationError
from chainura.rendering.models import EXCHANGE_IMAGE_SPECS

MAX_TEMPLATE_BYTES=12*1024*1024
@dataclass(frozen=True,slots=True)
class StoredTemplateAsset:
 exchange:Exchange;storage_key:str;sha256:str;width:int;height:int;content_type:str="image/png"
@dataclass(frozen=True,slots=True)
class ResolvedTemplate:
 exchange:Exchange;path:Path;sha256:str;storage_key:str;is_destination_specific:bool

class TemplateAssetStore:
 def __init__(self,root:Path|str,max_bytes:int=MAX_TEMPLATE_BYTES):
  self.root=Path(root).resolve();self.max_bytes=max_bytes
  if max_bytes<=0:raise TemplateValidationError("template byte limit must be positive")
 def validate(self,exchange:Exchange,data:bytes)->StoredTemplateAsset:
  if not isinstance(data,bytes) or not data or len(data)>self.max_bytes:raise TemplateValidationError("template bytes are empty or exceed the limit")
  try:
   with warnings.catch_warnings():
    warnings.simplefilter("error",Image.DecompressionBombWarning)
    with Image.open(io.BytesIO(data)) as image:
     spec=EXCHANGE_IMAGE_SPECS[exchange]
     if image.format!="PNG" or getattr(image,"n_frames",1)!=1:raise TemplateValidationError("template must be a single-frame PNG")
     if image.size!=(spec.width,spec.height):raise TemplateValidationError("template dimensions do not match exchange specification")
     image.verify()
  except TemplateValidationError:raise
  except (OSError,UnidentifiedImageError,Image.DecompressionBombWarning,Image.DecompressionBombError,KeyError) as exc:raise TemplateValidationError("template image is invalid") from exc
  digest=hashlib.sha256(data).hexdigest();key=f"sha256/{digest[:2]}/{digest}.png"
  return StoredTemplateAsset(exchange,key,digest,spec.width,spec.height)
 def persist(self,asset:StoredTemplateAsset,data:bytes)->Path:
  path=(self.root/asset.storage_key).resolve()
  if not path.is_relative_to(self.root):raise TemplateAssetError("template storage key escapes configured root")
  temporary=None
  try:
   path.parent.mkdir(parents=True,exist_ok=True)
   if path.exists():
    if hashlib.sha256(path.read_bytes()).hexdigest()!=asset.sha256:raise TemplateAssetError("content-addressed asset collision")
    return path
   with tempfile.NamedTemporaryFile(dir=path.parent,prefix=".template-",suffix=".tmp",delete=False) as stream:
    temporary=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
   try:os.link(temporary,path)
   except FileExistsError:pass
   temporary.unlink();temporary=None
   return path
  except (OSError,TemplateAssetError) as exc:
   if isinstance(exc,TemplateAssetError):raise
   raise TemplateAssetError("template asset persistence failed") from exc
  finally:
   if temporary:temporary.unlink(missing_ok=True)
 def inspect(self,exchange,path,sha):
  try:data=path.read_bytes()
  except OSError as exc:raise TemplateAssetError("selected template asset is missing") from exc
  asset=self.validate(exchange,data)
  if asset.sha256!=sha:raise TemplateAssetError("selected template hash mismatch")

class TemplateService:
 def __init__(self,transactions,store):self.transactions=transactions;self.store=store
 async def install(self,exchange,data,*,destination_id=None):
  asset=self.store.validate(exchange,data);self.store.persist(asset,data)
  try:
   async with self.transactions() as tx:
    if destination_id is not None and await tx.destinations.get_by_id(destination_id) is None:raise TemplatePersistenceError("destination does not exist")
    await tx.templates.replace(asset,destination_id);await tx.commit()
  except TemplatePersistenceError:raise
  return asset
 async def remove_override(self,exchange,destination_id):
  async with self.transactions() as tx:await tx.templates.remove_override(exchange,destination_id);await tx.commit()
 async def resolve(self,exchange,destination_id=None):
  async with self.transactions() as tx:
   record=await tx.templates.get(exchange,destination_id) if destination_id is not None else None
   specific=record is not None
   if record is None:record=await tx.templates.get(exchange,None)
  if record is None:raise TemplateNotConfiguredError("default template is not configured")
  path=(self.store.root/record.storage_key).resolve()
  if not path.is_relative_to(self.store.root):raise TemplateAssetError("template metadata escapes configured root")
  self.store.inspect(exchange,path,record.sha256)
  return ResolvedTemplate(exchange,path,record.sha256,record.storage_key,specific)
