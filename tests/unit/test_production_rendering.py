import io,tempfile,unittest
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

NOW=datetime(2026,8,4,12,tzinfo=timezone.utc)
def png(exchange):
 spec=EXCHANGE_IMAGE_SPECS[exchange];stream=io.BytesIO();Image.new("RGB",(spec.width,spec.height)).save(stream,"PNG");return stream.getvalue()
def signal():
 return Signal(1,2,"token",SignalMode.COMPOSITE,"BTCUSDT","BTC","USDT",None,TradeDirection.LONG,
  Decimal("100.001"),5,(Decimal("110.123"),),Decimal("90.321"),Decimal("100.002"),
  PriceSourceType.COMPOSITE,SignalLifecycleStatus.ACTIVE,tuple((x,"BTCUSDT") for x in Exchange),NOW,NOW)

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
   self.assertEqual(payload.entry_price,Decimal("100.001"));self.assertEqual(payload.display_price,Decimal("100.002"))
   self.assertEqual(payload.initial_pnl_percent,Decimal("0.00"));self.assertEqual(payload.elapsed_seconds,0);self.assertEqual(payload.coin_logo_path,logo)
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

if __name__=="__main__":unittest.main()
