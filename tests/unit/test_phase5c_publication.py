import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from chainura.application.initial_caption import build_initial_caption, TELEGRAM_PHOTO_CAPTION_MAX_LENGTH
from chainura.domain.destination import Exchange
from chainura.domain.publication import select_initial_exchange, tehran_local_date
from chainura.domain.signal import SignalMode, TradeDirection
from chainura.errors import CaptionGenerationError

class CaptionTests(unittest.TestCase):
 def signal(self,direction=TradeDirection.LONG,tps=(Decimal("1.2300"),),symbol="BTCUSDT"):
  return SimpleNamespace(direction=direction,take_profits=tps,canonical_symbol=symbol,
   entry_price=Decimal("1.00000001"),leverage=25,stop_loss=Decimal("0.99999999"))
 def test_long_private_exact_and_bidi(self):
  caption=build_initial_caption(self.signal(),private=True)
  self.assertIn("لانگ",caption);self.assertIn("#پیامک",caption)
  self.assertIn("\u2066<code>1.00000001</code>\u2069",caption)
  self.assertIn("BTCUSDT",caption);self.assertNotIn("reference",caption.lower())
 def test_short_public_tp_order(self):
  signal=self.signal(TradeDirection.SHORT,(Decimal("0.9"),Decimal("0.80")))
  signal.stop_loss=Decimal("1.1")
  caption=build_initial_caption(signal,private=False)
  self.assertIn("شورت",caption);self.assertNotIn("#پیامک",caption)
  self.assertLess(caption.index("0.9"),caption.index("0.80"))
 def test_html_escape(self):
  caption=build_initial_caption(self.signal(symbol="A&B"),private=False)
  self.assertIn("A&amp;B",caption);self.assertNotIn("A&B",caption)
 def test_caption_limit(self):
  with self.assertRaises(CaptionGenerationError):
   build_initial_caption(self.signal(tps=tuple(Decimal(f"1.{i:01000d}") for i in range(2))),private=False)

class PlanningTests(unittest.TestCase):
 def test_composite_priority_and_degraded_sources(self):
  self.assertEqual(select_initial_exchange(SignalMode.COMPOSITE,None,
   (Exchange.OURBIT,Exchange.TOOBIT,Exchange.LBANK),(Exchange.TOOBIT,Exchange.LBANK)),Exchange.TOOBIT)
 def test_single_compatibility_and_none(self):
  self.assertEqual(select_initial_exchange(SignalMode.SINGLE_EXCHANGE,Exchange.LBANK,
   (Exchange.OURBIT,Exchange.LBANK),(Exchange.LBANK,)),Exchange.LBANK)
  self.assertIsNone(select_initial_exchange(SignalMode.SINGLE_EXCHANGE,Exchange.LBANK,
   (Exchange.OURBIT,),(Exchange.LBANK,)))
 def test_tehran_rollover(self):
  self.assertEqual(str(tehran_local_date(datetime(2026,1,1,20,31,tzinfo=timezone.utc))),"2026-01-02")

class TemplateAdministrationTests(unittest.IsolatedAsyncioTestCase):
 async def test_mutations_durably_unblock_before_wake(self):
  from chainura.application.template_admin import TemplateAdministrationService
  events=[]
  class Templates:
   async def install(self,*a,**kw):events.append("install");return "asset"
   async def remove_override(self,*a):events.append("remove")
   async def configured(self,*a,**kw):return True
  class Publication:
   async def unblock_templates(self):events.append("unblock")
  service=TemplateAdministrationService(Templates(),Publication(),lambda:events.append("wake"))
  await service.install(Exchange.OURBIT,b"png")
  self.assertEqual(events,["install","unblock","wake"])
  events.clear();await service.remove_override(Exchange.OURBIT,1)
  self.assertEqual(events,["remove","unblock","wake"])

 async def test_bounded_document_download_and_rejections(self):
  import io
  from chainura.application.template_admin import download_png_document
  from chainura.errors import TemplateUploadTransportError
  class Bot:
   async def get_file(self,_):return SimpleNamespace(file_path="remote-id")
   async def download_file(self,_):return io.BytesIO(b"abc")
  doc=SimpleNamespace(file_id="id",mime_type="image/png",file_size=3)
  self.assertEqual(await download_png_document(Bot(),doc,max_bytes=3),b"abc")
  with self.assertRaises(TemplateUploadTransportError):
   await download_png_document(Bot(),SimpleNamespace(file_id="id",mime_type="image/jpeg",file_size=3),max_bytes=3)
  with self.assertRaises(TemplateUploadTransportError):
   await download_png_document(Bot(),SimpleNamespace(file_id="id",mime_type="image/png",file_size=4),max_bytes=3)
  class Large(Bot):
   async def download_file(self,_):return io.BytesIO(b"abcd")
  with self.assertRaises(TemplateUploadTransportError):await download_png_document(Large(),doc,max_bytes=3)

class WorkerTests(unittest.IsolatedAsyncioTestCase):
 async def test_delivery_failure_is_isolated_and_worker_continues(self):
  import asyncio
  from chainura.application.publication import InitialPublicationWorker
  class Service:
   def __init__(self):self.calls=[];self.round=0
   async def plan_pending(self):pass
   async def pending_ids(self):
    self.round+=1
    return (1,2) if self.round==1 else ()
   async def process(self,item):
    self.calls.append(item)
    if item==1:raise RuntimeError("secret")
  service=Service();worker=InitialPublicationWorker(service,concurrency=2,sweep_seconds=.1)
  await worker.start();await asyncio.sleep(.02);await worker.close()
  self.assertEqual(service.calls,[1,2])

 async def test_runtime_observes_unexpected_worker_exit(self):
  import asyncio
  from chainura.config import ApplicationSettings,TelegramRuntimeSettings
  from chainura.telegram.runtime import Runtime,RuntimeState
  runtime=Runtime(TelegramRuntimeSettings(ApplicationSettings("sqlite+aiosqlite:///x.db",1),"token","redis://localhost/1"))
  runtime.state=RuntimeState.RUNNING
  task=asyncio.create_task(asyncio.sleep(0));await task
  runtime._publication_done(task)
  self.assertTrue(runtime.shutdown_requested.is_set())
