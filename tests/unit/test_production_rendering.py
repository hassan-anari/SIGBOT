import io,tempfile,unittest,inspect,asyncio,threading
from unittest.mock import patch
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from PIL import Image
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType,Signal,SignalLifecycleStatus,SignalMode,TradeDirection
from chainura.application.initial_rendering import InitialPayloadFactory,InitialImageService
from chainura.errors import RenderingLifecycleError,TemplateValidationError,UnsafeRenderPathError
from chainura.rendering.logos import CoinLogoStore
from chainura.rendering.models import EXCHANGE_IMAGE_SPECS
from chainura.rendering.production import ProductionRendererRegistration,RenderingSettings
from chainura.rendering.pillow.text import DefaultTestFontProvider
from chainura.rendering.signal_image import unregister_signal_renderer
from chainura.rendering.templates import TemplateAssetStore
from chainura.application.profit import leveraged_return_percent
from chainura.rendering.pillow.formatting import format_initial_pnl
from chainura.rendering.pillow.compositor import _values
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
  item=replace(signal(),canonical_symbol="TONUSDT",
      exchange_symbols=((Exchange.OURBIT,"TON_USDT"),(Exchange.TOOBIT,"TON-SWAP-USDT"),(Exchange.LBANK,"ton_usdt")))
  payload=InitialPayloadFactory(CoinLogoStore("unused")).build(item,Exchange.TOOBIT,NOW)
  self.assertEqual(payload.native_symbol,"TON-SWAP-USDT");self.assertEqual(_values(payload)["symbol"],"TONUSDT")
  self.assertNotIn("TON-SWAP-USDT",_values(payload).values())
 def test_missing_snapshot_and_production_injection_are_controlled(self):
  from dataclasses import replace
  item=replace(signal(),initial_source_prices=((Exchange.OURBIT,Decimal("100")),(Exchange.LBANK,Decimal("100"))))
  with self.assertRaises(RenderPayloadError):InitialPayloadFactory(CoinLogoStore("unused")).build(item,Exchange.TOOBIT,NOW)
  parameters=inspect.signature(render_initial).parameters
  self.assertNotIn("layout",parameters);self.assertNotIn("value_overrides",parameters);self.assertNotIn("debug",parameters)

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
    if service._cleanup_tasks:await asyncio.gather(*tuple(service._cleanup_tasks))
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
