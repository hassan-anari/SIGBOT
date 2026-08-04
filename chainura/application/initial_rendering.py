from __future__ import annotations
import asyncio,uuid,logging
from datetime import datetime
from pathlib import Path
from chainura.domain.destination import Exchange
from chainura.domain.signal import Signal,SignalMode
from chainura.errors import RenderPayloadError,RenderingError,UnsafeRenderPathError
from chainura.rendering.logos import CoinLogoStore
from chainura.rendering.models import SignalRenderEventKind,SignalRenderPayload,RenderedSignalImage
from chainura.rendering.signal_image import render_signal_image
from chainura.application.profit import leveraged_return_percent
LOGGER=logging.getLogger("chainura.rendering.initial")
def _observe_future(future):
 try:future.exception()
 except (asyncio.CancelledError,Exception):pass

class InitialPayloadFactory:
 def __init__(self,logo_store:CoinLogoStore):self.logo_store=logo_store
 def build(self,signal:Signal,exchange:Exchange,rendered_at:datetime)->SignalRenderPayload:
  if signal.id is None or signal.id<1:raise RenderPayloadError("persisted signal is required")
  if not isinstance(rendered_at,datetime) or rendered_at.tzinfo is None or rendered_at.utcoffset() is None:raise RenderPayloadError("render instant must be timezone-aware")
  mappings=[symbol for mapped,symbol in signal.exchange_symbols if mapped is exchange]
  if len(mappings)!=1:raise RenderPayloadError("exchange has no unique native-symbol mapping")
  if signal.mode is SignalMode.SINGLE_EXCHANGE and signal.selected_exchange is not exchange:raise RenderPayloadError("exchange is incompatible with single-exchange signal")
  prices=[price for mapped,price in signal.initial_source_prices if mapped is exchange]
  if len(prices)!=1:raise RenderPayloadError("exchange has no unique initial price snapshot")
  logo=self.logo_store.resolve(exchange,mappings[0]) if exchange is Exchange.TOOBIT else None
  pnl=leveraged_return_percent(signal.entry_price,prices[0],signal.leverage,signal.direction)
  try:return SignalRenderPayload(signal.id,SignalRenderEventKind.INITIAL,exchange,signal.direction,
   signal.price_source_type,signal.canonical_symbol,mappings[0],signal.base_asset,signal.quote_asset,
   signal.entry_price,signal.leverage,signal.take_profits,signal.stop_loss,signal.reference_price,
   prices[0],pnl,signal.created_at,rendered_at,0,logo)
  except Exception as exc:
   if isinstance(exc,RenderPayloadError):raise
   raise RenderPayloadError("persisted signal is inconsistent with rendering contract") from exc

class InitialImageService:
 """Final images belong to the caller and must be cleaned after publication/abandonment."""
 def __init__(self,payload_factory,template_service,output_root:Path|str,concurrency:int):
  if concurrency<=0:raise RenderingError("render concurrency must be positive")
  self.payload_factory=payload_factory;self.template_service=template_service
  self.output_root=Path(output_root).resolve();self.output_root.mkdir(parents=True,exist_ok=True)
  self._semaphore=asyncio.Semaphore(concurrency);self._cleanup_tasks=set()
 def _new_output(self):return self.output_root/f"initial-{uuid.uuid4().hex}.png"
 async def render_initial_signal(self,signal,exchange,destination_id,rendered_at)->RenderedSignalImage:
  template=await self.template_service.resolve(exchange,destination_id)
  output=self._new_output()
  try:
   async with self._semaphore:
    payload=await asyncio.to_thread(self.payload_factory.build,signal,exchange,rendered_at)
    worker=asyncio.create_task(asyncio.to_thread(render_signal_image,payload,template.path,output));worker.add_done_callback(_observe_future)
    shielded=asyncio.shield(worker);shielded.add_done_callback(_observe_future)
    try:return await shielded
    except asyncio.CancelledError:
     cleanup=asyncio.create_task(self._finish_cancelled(worker,output))
     self._cleanup_tasks.add(cleanup);cleanup.add_done_callback(self._cleanup_tasks.discard)
     raise
  except asyncio.CancelledError:
   await self._safe_cleanup(output);raise
  except Exception:
   await self._safe_cleanup(output);raise
 async def _finish_cancelled(self,worker,output):
  try:await worker
  except BaseException:pass
  await self._safe_cleanup(output)
 async def _safe_cleanup(self,output):
  try:await asyncio.to_thread(self.cleanup,output)
  except Exception as exc:LOGGER.warning("render cleanup failed category=%s",type(exc).__name__)
 def cleanup(self,path:Path|str):
  candidate=Path(path).resolve()
  if not candidate.is_relative_to(self.output_root) or candidate==self.output_root:raise UnsafeRenderPathError("render cleanup path is outside output root")
  try:
   candidate.unlink(missing_ok=True)
   for temporary in candidate.parent.glob(f".{candidate.name}.*.tmp"):temporary.unlink(missing_ok=True)
  except OSError as exc:raise RenderingError("render output cleanup failed") from exc
