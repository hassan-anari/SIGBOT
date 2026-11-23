import io,tempfile,unittest,inspect,asyncio,threading,os
from unittest.mock import patch
import errno
import hashlib
from dataclasses import replace
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
from PIL import Image
from chainura.domain.destination import Exchange
from chainura.domain.publication import LimitTriggerPrices
from chainura.domain.signal import PriceSourceType,Signal,SignalEntryType,SignalLifecycleStatus,SignalMode,TradeDirection
from chainura.application.initial_rendering import InitialPayloadFactory,InitialImageService,UpdatePayloadFactory
from chainura.application.message_templates import initial_context
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
   self.assertEqual(_values(payload)["price"], "100.003")
   self.assertEqual(_values(replace(payload, initial_pnl_percent=Decimal("0")))["pnl"], "+0.00%")
   context = initial_context(signal(), Exchange.TOOBIT, private=False)
   self.assertEqual(context["last_price"], "\u2066<code>100.003</code>\u2069")
   self.assertEqual(context["pnl"], "\u2066<code>0.01%</code>\u2069")
 def test_limit_initial_uses_trigger_quote_for_long_and_short(self):
  entry = Decimal("0.08593")
  with tempfile.TemporaryDirectory() as root:
   factory = InitialPayloadFactory(CoinLogoStore(root))
   for direction, creation, target, stop, expected_pnls in (
       (TradeDirection.LONG, "0.09035", "0.095", "0.08", ("0.00", "-6.49", "7.47")),
       (TradeDirection.SHORT, "0.08035", "0.075", "0.09", ("0.00", "6.49", "-7.47")),
   ):
    limit = replace(signal(), id=1257, canonical_symbol="LISTAUSDT", base_asset="LISTA",
        entry_type=SignalEntryType.LIMIT, direction=direction, entry_price=entry, leverage=6,
        take_profits=(Decimal(target),), stop_loss=Decimal(stop), reference_price=Decimal(creation),
        exchange_symbols=tuple((exchange, "LISTAUSDT") for exchange in Exchange),
        initial_source_prices=tuple((exchange, Decimal(creation)) for exchange in Exchange))
    for quote, expected_pnl in zip(("0.08593", "0.085", "0.087"), expected_pnls):
     for exchange in Exchange:
      with self.subTest(direction=direction, exchange=exchange, quote=quote):
       prices = LimitTriggerPrices(entry, Decimal(quote))
       payload = factory.build(limit, exchange, NOW, trigger_prices=prices)
       self.assertEqual((payload.entry_price, payload.reference_price, payload.display_price),
                        (entry, entry, Decimal(quote)))
       self.assertEqual(payload.initial_pnl_percent, leveraged_return_percent(entry, Decimal(quote), 6, direction))
       self.assertEqual((payload.leverage, payload.take_profits, payload.stop_loss),
                        (limit.leverage, limit.take_profits, limit.stop_loss))
       values = _values(payload)
       image_pnl = f"{Decimal(expected_pnl):+.2f}" if expected_pnl != "0.00" or exchange is Exchange.TOOBIT else expected_pnl
       self.assertEqual((values["entry"], values["price"], values["pnl"]), ("0.08593", quote, f"{image_pnl}%"))
       context = initial_context(limit, exchange, private=False, trigger_prices=prices)
       self.assertEqual((context["entry"], context["last_price"], context["pnl"]),
                        ("\u2066<code>0.08593</code>\u2069", f"\u2066<code>{quote}</code>\u2069",
                         f"\u2066<code>{expected_pnl}%</code>\u2069"))
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
 def make_service(self,root,concurrency=1):
  class Templates:
   async def resolve(self,*_):return type("T",(),{"path":Path(root)/"template.png"})()
  return InitialImageService(InitialPayloadFactory(CoinLogoStore(root)),Templates(),Path(root)/"out",concurrency)
 async def wait_for_files(self,service,count=1):
  for _ in range(200):
   files=list(service.output_root.iterdir())
   if len(files)>=count:return files
   await asyncio.sleep(.005)
  self.fail("output reservation was not created")
 async def test_cancellation_during_reservation_is_supervised(self):
  with tempfile.TemporaryDirectory() as root:
   service=self.make_service(root);entered=threading.Event();release=threading.Event()
   original=service._reserve_output
   def reserve():entered.set();release.wait(5);return original()
   service._reserve_output=reserve
   errors=[];loop=asyncio.get_running_loop();previous=loop.get_exception_handler()
   loop.set_exception_handler(lambda _loop,context:errors.append(context))
   try:
    task=asyncio.create_task(service.render_initial_signal(signal(),Exchange.OURBIT,None,NOW))
    self.assertTrue(await asyncio.to_thread(entered.wait,2));task.cancel()
    with self.assertRaises(asyncio.CancelledError):await task
    release.set();await service.aclose()
   finally:loop.set_exception_handler(previous);release.set()
   self.assertEqual(list(service.output_root.iterdir()),[]);self.assertEqual(errors,[])
 async def test_worker_cancellation_eventually_removes_output_and_propagates(self):
  with tempfile.TemporaryDirectory() as root:
   service=self.make_service(root)
   started=threading.Event();release=threading.Event()
   def worker(payload,template,output):
    started.set();release.wait(5);Path(output).write_bytes(b"png")
    temporary=Path(output).parent/f".{Path(output).name}.worker.tmp";temporary.write_bytes(b"tmp")
    raise RuntimeError("worker failed")
   with patch("chainura.application.initial_rendering.render_signal_image",worker):
    task=asyncio.create_task(service.render_initial_signal(signal(),Exchange.OURBIT,None,NOW))
    self.assertTrue(await asyncio.to_thread(started.wait,2));reserved=(await self.wait_for_files(service))[0]
    task.cancel();task.cancel()
    with self.assertRaises(asyncio.CancelledError):await task
    self.assertTrue(reserved.exists())
    with self.assertRaises(FileExistsError):os.open(reserved,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    release.set()
    await service.aclose()
    self.assertEqual(list(service.output_root.iterdir()),[])
 async def test_cancellation_before_render_permit_leaves_nothing(self):
  with tempfile.TemporaryDirectory() as root:
   service=self.make_service(root)
   await service._semaphore.acquire()
   task=asyncio.create_task(service.render_initial_signal(signal(),Exchange.OURBIT,None,NOW))
   await self.wait_for_files(service);task.cancel()
   with self.assertRaises(asyncio.CancelledError):await task
   service._semaphore.release();await service.aclose()
   self.assertEqual(list(service.output_root.iterdir()),[])
 async def test_concurrent_close_drains_active_worker(self):
  with tempfile.TemporaryDirectory() as root:
   service=self.make_service(root);started=threading.Event();release=threading.Event()
   def worker(*args):started.set();release.wait(5);Path(args[2]).write_bytes(b"done");return type("R",(),{"file_path":Path(args[2])})()
   with patch("chainura.application.initial_rendering.render_signal_image",worker):
    render=asyncio.create_task(service.render_initial_signal(signal(),Exchange.OURBIT,None,NOW))
    self.assertTrue(await asyncio.to_thread(started.wait,2));render.cancel()
    with self.assertRaises(asyncio.CancelledError):await render
    closes=[asyncio.create_task(service.aclose()) for _ in range(2)]
    await asyncio.sleep(0);self.assertTrue(all(not item.done() for item in closes));release.set()
    await asyncio.gather(*closes)
   self.assertEqual(list(service.output_root.iterdir()),[]);self.assertEqual(service._owned_tasks,set())
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
   await service.aclose();self.assertEqual(result.file_path.read_bytes(),b"rendered")
   service.cleanup(result.file_path);self.assertFalse(result.file_path.exists())

class Phase6BUpdateRenderingTests(unittest.IsolatedAsyncioTestCase):
 def event(self,kind,price=Decimal('111'),tp_index=1,seconds=3661):
  from chainura.domain.monitoring import SignalEventKind
  roi=Decimal('0') if kind is SignalEventKind.BREAKEVEN else Decimal('55')
  return type('E',(),{'id':1,'kind':kind,'tp_index':tp_index if kind is SignalEventKind.TP_HIT else None,'observed_reference_price':price,'roi_basis_price':price,'leveraged_roi':roi,'occurred_at':NOW.replace(hour=13,minute=1,second=1)})()
 def service(self,root,concurrency=2):
  class Templates:
   async def resolve(self,*_):return type('T',(),{'path':Path(root)/'template.png'})()
  return InitialImageService(InitialPayloadFactory(CoinLogoStore(root)),Templates(),Path(root)/'out',concurrency)
 def test_update_payload_uses_persisted_execution_basis_and_roi(self):
  from chainura.domain.monitoring import SignalEventKind
  with tempfile.TemporaryDirectory() as root:
   factory=UpdatePayloadFactory(CoinLogoStore(root))
   limit = replace(signal(), entry_type=SignalEntryType.LIMIT)
   tp=self.event(SignalEventKind.TP_HIT,Decimal('111'))
   tp.roi_basis_price=Decimal('110');tp.leveraged_roi=Decimal('49.49950500494995050049499505')
   sl=self.event(SignalEventKind.SL_HIT,Decimal('89'))
   sl.roi_basis_price=Decimal('90.321');sl.leveraged_roi=Decimal('-48.39951600483995160048399516')
   for trade in (signal(), limit):
    for event, display, pnl in ((tp, "110", "+49.50%"), (sl, "90.321", "-48.40%")):
     with self.subTest(entry_type=trade.entry_type, event=event.kind):
      payload=factory.build_update(trade,event,Exchange.OURBIT)
      self.assertEqual(payload.display_price,event.roi_basis_price)
      self.assertEqual(payload.initial_pnl_percent,event.leveraged_roi)
      self.assertEqual((_values(payload)["price"], _values(payload)["pnl"]), (display, pnl))
   be=self.event(SignalEventKind.BREAKEVEN,Decimal('101'),None)
   be.roi_basis_price=signal().entry_price
   payload=factory.build_update(signal(),be,Exchange.OURBIT)
   self.assertEqual(payload.display_price,signal().entry_price)
   self.assertEqual(payload.initial_pnl_percent,Decimal('0'))
 async def test_update_output_prefix_metadata_and_cleanup(self):
  from chainura.domain.monitoring import SignalEventKind
  with tempfile.TemporaryDirectory() as root:
   service=self.service(root)
   spec=EXCHANGE_IMAGE_SPECS[Exchange.OURBIT]
   def renderer(payload,template,output):
    self.assertEqual(payload.event_kind.value,'tp');self.assertGreater(payload.elapsed_seconds,0)
    Path(output).write_bytes(b'update-bytes');return __import__('chainura.rendering.models',fromlist=['RenderedSignalImage']).RenderedSignalImage(Path(output),spec.width,spec.height,spec.content_type,hashlib.sha256(b'update-bytes').hexdigest())
   with patch('chainura.application.initial_rendering.render_signal_image',renderer):
    result=await service.render_update_signal(signal(),self.event(SignalEventKind.TP_HIT),Exchange.OURBIT,None)
   self.assertTrue(result.file_path.exists());self.assertTrue(result.file_path.name.startswith('update-'))
   self.assertEqual((result.width,result.height,result.content_type),(spec.width,spec.height,spec.content_type))
   self.assertEqual(result.sha256,hashlib.sha256(result.file_path.read_bytes()).hexdigest())
   self.assertFalse(list(service.output_root.glob('initial-*.png')))
   service.cleanup(result.file_path);self.assertFalse(result.file_path.exists())
 async def test_concurrent_initial_and_two_updates_do_not_mutate_factory(self):
  from chainura.domain.monitoring import SignalEventKind
  with tempfile.TemporaryDirectory() as root:
   service=self.service(root,3);seen=[]
   def renderer(payload,template,output):
    seen.append((payload.event_kind.value,payload.display_price,Path(output).name.split('-',1)[0]));Path(output).write_bytes(str(payload.display_price).encode())
    spec=EXCHANGE_IMAGE_SPECS[payload.exchange]
    return __import__('chainura.rendering.models',fromlist=['RenderedSignalImage']).RenderedSignalImage(Path(output),spec.width,spec.height,spec.content_type,hashlib.sha256(Path(output).read_bytes()).hexdigest())
   with patch('chainura.application.initial_rendering.render_signal_image',renderer):
    a,b,c=await asyncio.gather(service.render_initial_signal(signal(),Exchange.OURBIT,None,NOW),service.render_update_signal(signal(),self.event(SignalEventKind.SL_HIT,Decimal('90'),None),Exchange.OURBIT,None),service.render_update_signal(signal(),self.event(SignalEventKind.BREAKEVEN,Decimal('100'),None),Exchange.OURBIT,None))
   self.assertEqual(a.file_path.name.split('-',1)[0],'initial');self.assertTrue(b.file_path.name.startswith('update-'));self.assertTrue(c.file_path.name.startswith('update-'))
   self.assertIn(('initial',Decimal('100.002'),'initial'),seen);self.assertIn(('sl',Decimal('90'),'update'),seen);self.assertIn(('breakeven',Decimal('100'),'update'),seen)
   self.assertIsInstance(service.payload_factory,InitialPayloadFactory)
