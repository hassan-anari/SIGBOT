from __future__ import annotations
import asyncio,hashlib,io,os,tempfile,warnings
from dataclasses import dataclass
from pathlib import Path
from PIL import Image,UnidentifiedImageError
from chainura.domain.destination import Exchange
from chainura.errors import (TemplateAssetError,TemplateNotConfiguredError,
    TemplatePersistenceError,TemplateValidationError)
from chainura.rendering.models import EXCHANGE_IMAGE_SPECS

MAX_TEMPLATE_BYTES=12*1024*1024
def validate_destination_id(value):
    if isinstance(value,bool) or not isinstance(value,int) or value<=0:
        raise TemplateValidationError("destination ID must be a positive integer")
    return value

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
        if not isinstance(data,bytes) or not data or len(data)>self.max_bytes:
            raise TemplateValidationError("template bytes are empty or exceed the limit")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error",Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    spec=EXCHANGE_IMAGE_SPECS[exchange]
                    if image.format!="PNG" or getattr(image,"n_frames",1)!=1:
                        raise TemplateValidationError("template must be a single-frame PNG")
                    if image.size!=(spec.width,spec.height):
                        raise TemplateValidationError("template dimensions do not match exchange specification")
                    image.verify()
        except TemplateValidationError:raise
        except (OSError,UnidentifiedImageError,Image.DecompressionBombWarning,
                Image.DecompressionBombError,KeyError) as exc:
            raise TemplateValidationError("template image is invalid") from exc
        digest=hashlib.sha256(data).hexdigest()
        return StoredTemplateAsset(exchange,f"sha256/{digest[:2]}/{digest}.png",digest,spec.width,spec.height)
    @staticmethod
    def _hash(path):
        digest=hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
        return digest.hexdigest()
    def persist(self,asset,data):
        path=(self.root/asset.storage_key).resolve()
        if not path.is_relative_to(self.root):raise TemplateAssetError("template storage key escapes configured root")
        temporary=None
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent,prefix=".template-",suffix=".tmp",delete=False) as stream:
                temporary=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
            try:os.link(temporary,path)
            except FileExistsError:pass
            if self._hash(path)!=asset.sha256:raise TemplateAssetError("content-addressed asset collision")
            directory=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
            return path
        except TemplateAssetError:raise
        except OSError as exc:raise TemplateAssetError("template asset persistence failed") from exc
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
    def inspect(self,exchange,path,sha):
        try:data=path.read_bytes()
        except OSError as exc:raise TemplateAssetError("selected template asset is missing") from exc
        asset=self.validate(exchange,data)
        if asset.sha256!=sha:raise TemplateAssetError("selected template hash mismatch")

class TemplateService:
    def __init__(self,transactions,store,concurrency=2):
        if concurrency<=0:raise TemplateValidationError("asset concurrency must be positive")
        self.transactions=transactions;self.store=store;self._local=asyncio.Semaphore(concurrency)
    async def _local_call(self,call,*args):
        async with self._local:return await asyncio.to_thread(call,*args)
    async def install(self,exchange,data,*,destination_id=None):
        if destination_id is not None:validate_destination_id(destination_id)
        asset=await self._local_call(self.store.validate,exchange,data)
        await self._local_call(self.store.persist,asset,data)
        async with self.transactions() as tx:
            tx.use_template_errors()
            if destination_id is not None and await tx.destinations.get_by_id(destination_id) is None:
                raise TemplatePersistenceError("destination does not exist")
            await tx.templates.replace(asset,destination_id);await tx.commit()
        return asset
    async def remove_override(self,exchange,destination_id):
        validate_destination_id(destination_id)
        async with self.transactions() as tx:
            tx.use_template_errors();await tx.templates.remove_override(exchange,destination_id);await tx.commit()
    async def resolve(self,exchange,destination_id=None):
        if destination_id is not None:validate_destination_id(destination_id)
        async with self.transactions() as tx:
            tx.use_template_errors()
            metadata=await tx.templates.get(exchange,destination_id) if destination_id is not None else None
            specific=metadata is not None
            if metadata is None:metadata=await tx.templates.get(exchange,None)
        if metadata is None:raise TemplateNotConfiguredError("default template is not configured")
        path=(self.store.root/metadata.storage_key).resolve()
        if not path.is_relative_to(self.store.root):raise TemplateAssetError("template metadata escapes configured root")
        await self._local_call(self.store.inspect,exchange,path,metadata.sha256)
        return ResolvedTemplate(exchange,path,metadata.sha256,metadata.storage_key,specific)
