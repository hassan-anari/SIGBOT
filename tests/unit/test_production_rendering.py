import io,tempfile,unittest,inspect,asyncio,threading
from unittest.mock import patch
import errno
import hashlib
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from PIL import Image
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType,Signal,SignalLifecycleStatus,SignalMode,TradeDirection
from chainura.application.initial_rendering import InitialPayloadFactory,InitialImageService
from chainura.errors import (RenderingLifecycleError,TemplateValidationError,UnsafeRenderPathError,
    TemplatePersistenceError,TemplateAssetError)
from chainura.rendering.logos import CoinLogoStore,toobit_logo_key
from chainura.rendering.models import EXCHANGE_IMAGE_SPECS
from chainura.rendering.production import ProductionRendererRegistration,RenderingSettings
from chainura.rendering.pillow.text import DefaultTestFontProvider
from chainura.rendering.signal_image import unregister_signal_renderer
from chainura.rendering.templates import TemplateAssetStore,TemplateService,MAX_TEMPLATE_BYTES
from chainura.application.profit import leveraged_return_percent
from chainura.rendering.pillow.formatting import format_initial_pnl
from chainura.rendering.pillow.compositor import _values,pnl_semantic_state
from chainura.rendering.pillow.text import _resolved_color
from chainura.rendering.core import render_initial
from chainura.errors import RenderPayloadError

NOW=datetime(2026,8,4,12,tzinfo=timezone.utc)
def png(exchange):
 spec=EXCHANGE_IMAGE_SPECS[exchange];stream=io.BytesIO();Image.new("RGB",(spec.width,spec.height)).save(stream,"PNG");return stream.getvalue()
def signal():
 return Signal(1,2,"token",SignalMode.COMPOSITE,"BTCUSDT","BTC","USDT",None,TradeDirection.LONG,
  Decimal("100.001"),5,(Decimal("110.123"),),Decimal("90.321"),Decimal("100.002"),
  PriceSourceType.COMPOSITE,SignalLifecycleStatus.ACTIVE,tuple((x,"BTCUSDT") for x in Exchange),NOW,NOW,
  ((Exchange.OURBIT,Decimal("100.002")),(Exchange.TOOBIT,Decimal("100.003")),(Exchange.LBANK,Decimal("100.004"))))

class ProductionRenderingTests(unittest.TestCase):
 def tearDown(self):
  for exchange in Exchange:unregister_signal_renderer(exchange)
  ProductionRendererRegistration._active=None
 def test_template_validation_and_content_addressed_storage(self):
  with tempfile.TemporaryDirectory() as root:
   store=TemplateAssetStore(root);data=png(Exchange.OURBIT);asset=store.validate(Exchange.OURBIT,data)
   first=store.persist(asset,data);self.assertEqual(first,store.persist(asset,data));self.assertTrue(first.is_relative_to(Path(root)))
   self.assertEqual(asset.sha256,len(asset.sha256)*"x" if False else asset.sha256)
   with self.assertRaises(TemplateValidationError):store.validate(Exchange.OURBIT,b"not png")
   with self.assertRaises(TemplateValidationError):store.validate(Exchange.TOOBIT,data)
 def test_payload_factory_exact_initial_contract_and_local_logo(self):
  with tempfile.TemporaryDirectory() as root:
   store=CoinLogoStore(root);logo=store.path(Exchange.TOOBIT,"BTCUSDT");logo.parent.mkdir(parents=True);logo.write_bytes(png(Exchange.TOOBIT))
   factory=InitialPayloadFactory(store);payload=factory.build(signal(),Exchange.TOOBIT,NOW)
   self.assertEqual(payload.entry_price,Decimal("100.001"));self.assertEqual(payload.display_price,Decimal("100.003"))
   self.assertEqual(payload.initial_pnl_percent,(Decimal(".002")/Decimal("100.001")*100*5));self.assertEqual(payload.elapsed_seconds,0);self.assertEqual(payload.coin_logo_path,logo)
   self.assertIsNone(factory.build(signal(),Exchange.OURBIT,NOW).coin_logo_path)
 def test_explicit_registry_lifecycle(self):
  with tempfile.TemporaryDirectory() as root:
   path=Path(root);dummy=path/"font";dummy.write_text("test")
   settings=RenderingSettings(path/"assets",path/"outputs",path/"logos",dummy,dummy,dummy,dummy,2)
   registration=ProductionRendererRegistration(settings,DefaultTestFontProvider()).start()
   with self.assertRaises(RenderingLifecycleError):ProductionRendererRegistration(settings,DefaultTestFontProvider()).start()
   registration.close();registration.close()
 def test_cleanup_is_idempotent_and_root_bounded(self):
  with tempfile.TemporaryDirectory() as root:
   class Factory:pass
   service=InitialImageService(Factory(),Factory(),Path(root)/"out",1);path=service.output_root/"x.png";path.write_bytes(b"x")
   service.cleanup(path);service.cleanup(path)
   with self.assertRaises(UnsafeRenderPathError):service.cleanup(Path(root)/"elsewhere.png")
 def test_exact_profit_formula_and_sign_formatting(self):
  entry=Decimal("3");price=Decimal("3.0000000000000000001")
  expected=(price-entry)/entry*Decimal("100")*7
  self.assertEqual(leveraged_return_percent(entry,price,7,TradeDirection.LONG),expected)
  self.assertEqual(leveraged_return_percent(entry,price,7,TradeDirection.SHORT),-expected)
  self.assertEqual(format_initial_pnl(Decimal("12.345")),"+12.34%")
  self.assertEqual(format_initial_pnl(Decimal("-12.345")),"-12.34%")
  self.assertEqual(format_initial_pnl(Decimal("0")),"0.00%")
  self.assertNotIn("+-",format_initial_pnl(Decimal("-1")))
 def test_canonical_symbol_visible_native_symbol_logo_only(self):
  from dataclasses import replace
  with tempfile.TemporaryDirectory() as root:
   item=replace(signal(),canonical_symbol="TONUSDT",
       exchange_symbols=((Exchange.OURBIT,"TON_USDT"),(Exchange.TOOBIT,"TON-SWAP-USDT"),(Exchange.LBANK,"ton_usdt")))
   store=CoinLogoStore(root);logo=store.path(Exchange.TOOBIT,"TON-SWAP-USDT","TONUSDT")
   logo.parent.mkdir(parents=True);Image.new("RGBA",(16,16)).save(logo,"PNG")
   payload=InitialPayloadFactory(store).build(item,Exchange.TOOBIT,NOW)
   self.assertEqual(payload.native_symbol,"TON-SWAP-USDT");self.assertEqual(payload.coin_logo_path,logo)
   self.assertEqual(_values(payload)["symbol"],"TONUSDT")
   self.assertNotIn("TON-SWAP-USDT",_values(payload).values())
 def test_missing_snapshot_and_production_injection_are_controlled(self):
  from dataclasses import replace
  item=replace(signal(),initial_source_prices=((Exchange.OURBIT,Decimal("100")),(Exchange.LBANK,Decimal("100"))))
  with self.assertRaises(RenderPayloadError):InitialPayloadFactory(CoinLogoStore("unused")).build(item,Exchange.TOOBIT,NOW)
  parameters=inspect.signature(render_initial).parameters
  self.assertNotIn("layout",parameters);self.assertNotIn("value_overrides",parameters);self.assertNotIn("debug",parameters)
 def test_all_exchange_pnl_colors_are_semantic_for_arbitrary_and_tiny_values(self):
  from chainura.rendering.core import LAYOUTS
  for exchange in Exchange:
   layer=next(item for item in LAYOUTS[exchange].text_layers if item.field=="pnl")
   colors=dict(layer.fill_by_state)
   for value,state in ((Decimal("8.765"),"positive"),(Decimal("-7.654"),"negative"),
                       (Decimal("0"),"neutral"),(Decimal("0.00001"),"positive"),
                       (Decimal("-0.00001"),"negative")):
    self.assertEqual(pnl_semantic_state(value),state)
    self.assertEqual(_resolved_color(layer.fill,layer.fill_by_state,state),colors[state])
 def test_shared_toobit_logo_key_contract(self):
  for native,canonical in (("TON-SWAP-USDT","TONUSDT"),("BTC-SWAP-USDT","BTCUSDT"),("BTCUSDT","BTCUSDT")):
   self.assertEqual(toobit_logo_key(native),canonical)
  for unsafe in ("../BTC","BTC/USDT","BTC_SWAP_USDT","BTC-SWAP-USDT/../../x"):
   self.assertIsNone(toobit_logo_key(unsafe))
 def test_unsupported_directory_fsync_is_controlled(self):
  with patch("chainura.rendering.templates.os.open",side_effect=OSError(errno.ENOTSUP,"unsupported")):
   TemplateAssetStore._fsync_directory(Path("unused"))

class TemplateErrorBoundaryTests(unittest.IsolatedAsyncioTestCase):
 async def test_transaction_begin_failure_is_template_specific(self):
  class Broken:
   async def __aenter__(self):raise RuntimeError("database detail")
   async def __aexit__(self,*_):pass
  class Factory:
   def __call__(self):return Broken()
  with tempfile.TemporaryDirectory() as root:
   service=TemplateService(Factory(),TemplateAssetStore(root))
   with self.assertRaises(TemplatePersistenceError):await service.resolve(Exchange.OURBIT)

class BoundedTemplateInspectionTests(unittest.TestCase):
 def test_exact_limit_accepted_and_oversize_rejected_before_read(self):
  with tempfile.TemporaryDirectory() as root:
   store=TemplateAssetStore(root);base=png(Exchange.OURBIT)
   exact=Path(root)/"exact.png";data=base+b"\0"*(MAX_TEMPLATE_BYTES-len(base));exact.write_bytes(data)
   store.inspect(Exchange.OURBIT,exact,hashlib.sha256(data).hexdigest())
   large=Path(root)/"large.png";large.write_bytes(data+b"x")
   with self.assertRaises(TemplateAssetError):store.inspect(Exchange.OURBIT,large,"unused")
 def test_non_regular_and_no_unbounded_read_bytes(self):
  with tempfile.TemporaryDirectory() as root:
   store=TemplateAssetStore(root)
   with self.assertRaises(TemplateAssetError):store.inspect(Exchange.OURBIT,Path(root),"unused")
  self.assertNotIn("read_bytes",inspect.getsource(TemplateAssetStore.inspect))
 def test_growth_and_truncated_reads_are_rejected(self):
  base=png(Exchange.OURBIT)
  class ManagedPath:
   def __init__(self,data,reported):self.data=data;self.reported=reported
   def stat(self):return type("S",(),{"st_mode":0o100600,"st_size":self.reported})()
   def open(self,*_):return io.BytesIO(self.data)
  store=TemplateAssetStore("unused")
  for path in (ManagedPath(base+b"x",len(base)),ManagedPath(base,len(base)+1)):
   with self.assertRaises(TemplateAssetError):store.inspect(Exchange.OURBIT,path,"unused")
 def test_sparse_oversize_rejected_by_stat(self):
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/"sparse.png"
   with path.open("wb") as stream:stream.seek(MAX_TEMPLATE_BYTES);stream.write(b"x")
   with self.assertRaises(TemplateAssetError):TemplateAssetStore(root).inspect(Exchange.OURBIT,path,"unused")

if __name__=="__main__":unittest.main()

class CancellationTests(unittest.IsolatedAsyncioTestCase):
 async def test_worker_cancellation_eventually_removes_output_and_propagates(self):
  with tempfile.TemporaryDirectory() as root:
   class Templates:
    async def resolve(self,*_):return type("T",(),{"path":Path(root)/"template.png"})()
   service=InitialImageService(InitialPayloadFactory(CoinLogoStore(root)),Templates(),Path(root)/"out",1)
   started=threading.Event();release=threading.Event()
   def worker(payload,template,output):
    started.set();release.wait(5);Path(output).write_bytes(b"png");raise RuntimeError("worker failed")
   with patch("chainura.application.initial_rendering.render_signal_image",worker):
    task=asyncio.create_task(service.render_initial_signal(signal(),Exchange.OURBIT,None,NOW))
    await asyncio.to_thread(started.wait,2);task.cancel();task.cancel()
    with self.assertRaises(asyncio.CancelledError):await task
    release.set()
    await service.aclose()
    self.assertEqual(list(service.output_root.iterdir()),[])
 async def test_cancellation_before_render_permit_leaves_nothing(self):
  with tempfile.TemporaryDirectory() as root:
   class Templates:
    async def resolve(self,*_):return type("T",(),{"path":Path(root)/"template.png"})()
   service=InitialImageService(InitialPayloadFactory(CoinLogoStore(root)),Templates(),Path(root)/"out",1)
   await service._semaphore.acquire()
   task=asyncio.create_task(service.render_initial_signal(signal(),Exchange.OURBIT,None,NOW))
   await asyncio.sleep(0);task.cancel()
   with self.assertRaises(asyncio.CancelledError):await task
   service._semaphore.release();self.assertEqual(list(service.output_root.iterdir()),[])
 async def test_close_is_idempotent_and_rejects_new_render(self):
  with tempfile.TemporaryDirectory() as root:
   class Templates:pass
   service=InitialImageService(None,Templates(),Path(root)/"out",1)
   await asyncio.gather(service.aclose(),service.aclose())
   with self.assertRaises(RenderingLifecycleError):
    await service.render_initial_signal(None,Exchange.OURBIT,None,NOW)

class OutputOwnershipTests(unittest.IsolatedAsyncioTestCase):
 async def test_forced_collision_retries_and_preserves_unrelated_file(self):
  with tempfile.TemporaryDirectory() as root:
   class Templates:
    async def resolve(self,*_):return type("T",(),{"path":Path(root)/"template.png"})()
   service=InitialImageService(InitialPayloadFactory(CoinLogoStore(root)),Templates(),Path(root)/"out",1)
   unrelated=service.output_root/"initial-collision.png";unrelated.write_bytes(b"owner")
   names=iter([type("U",(),{"hex":"collision"})(),type("U",(),{"hex":"owned"})()])
   def renderer(payload,template,output):Path(output).write_bytes(b"rendered");return type("R",(),{"file_path":Path(output)})()
   with patch("chainura.application.initial_rendering.uuid.uuid4",side_effect=lambda:next(names)),patch("chainura.application.initial_rendering.render_signal_image",renderer):
    result=await service.render_initial_signal(signal(),Exchange.OURBIT,None,NOW)
   self.assertEqual(unrelated.read_bytes(),b"owner");self.assertEqual(result.file_path.name,"initial-owned.png")
   service.cleanup(result.file_path);await service.aclose()
