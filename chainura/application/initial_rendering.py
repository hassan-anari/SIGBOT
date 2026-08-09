from __future__ import annotations
import asyncio,uuid,logging,os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from chainura.domain.destination import Exchange
from chainura.domain.signal import Signal,SignalMode
from chainura.errors import (RenderPayloadError,RenderingError,UnsafeRenderPathError,
    RenderingLifecycleError)
from chainura.rendering.logos import CoinLogoStore
from chainura.rendering.models import SignalRenderEventKind,SignalRenderPayload,RenderedSignalImage
from chainura.rendering.signal_image import render_signal_image
from chainura.application.profit import leveraged_return_percent
LOGGER=logging.getLogger("chainura.rendering.initial")
def _observe_future(future):
 try:future.exception()
 except (asyncio.CancelledError,Exception):pass
async def _run_render_worker(payload,template,output):
 try:return True,await asyncio.to_thread(render_signal_image,payload,template,output)
 except BaseException as exc:return False,exc

@dataclass
class _OutputReservation:
 path:Path
 transferred:bool=False
 deferred:bool=False
 cleaned:bool=False
 def transfer(self):
  if self.deferred or self.cleaned:raise RenderingLifecycleError("render output ownership is unavailable")
  self.transferred=True
 def defer(self):
  if self.transferred or self.cleaned:raise RenderingLifecycleError("render output ownership is unavailable")
  self.deferred=True
 def mark_cleaned(self):
  if self.transferred:raise RenderingLifecycleError("caller-owned render output cannot be cleaned by service")
  self.cleaned=True

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
  logo=self.logo_store.resolve(exchange,mappings[0],signal.canonical_symbol) if exchange is Exchange.TOOBIT else None
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
  self._semaphore=asyncio.Semaphore(concurrency);self._owned_tasks=set()
  self._active_tasks=set();self._state="open";self._close_task=None
 def _reserve_output(self):
  return self._reserve_output_with_prefix("initial")
 def _reserve_output_with_prefix(self,prefix):
  if prefix not in ("initial","update"):raise RenderingLifecycleError("render output prefix is invalid")
  for _ in range(16):
   candidate=self.output_root/f"{prefix}-{uuid.uuid4().hex}.png"
   try:
    descriptor=os.open(candidate,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   except FileExistsError:continue
   except OSError as exc:raise RenderingError("render output reservation failed") from exc
   try:os.close(descriptor)
   except OSError as exc:
    candidate.unlink(missing_ok=True);raise RenderingError("render output reservation failed") from exc
   return _OutputReservation(candidate)
  raise RenderingLifecycleError("render output name allocation exhausted")
 def _track_owned(self,coroutine):
  task=asyncio.create_task(coroutine);self._owned_tasks.add(task)
  task.add_done_callback(self._owned_tasks.discard);task.add_done_callback(_observe_future)
  return task
 async def render_initial_signal(self,signal,exchange,destination_id,rendered_at)->RenderedSignalImage:
  return await self._render_with_builder(
   payload_builder=lambda: self.payload_factory.build(signal,exchange,rendered_at),
   exchange=exchange,destination_id=destination_id,output_prefix="initial")
 async def render_update_signal(self,signal,event,exchange,destination_id)->RenderedSignalImage:
  factory=self.payload_factory if hasattr(self.payload_factory,"build_update") else UpdatePayloadFactory(self.payload_factory.logo_store)
  return await self._render_with_builder(
   payload_builder=lambda: factory.build_update(signal,event,exchange,event.occurred_at),
   exchange=exchange,destination_id=destination_id,output_prefix="update")
 async def _render_with_builder(self,*,payload_builder,exchange,destination_id,output_prefix)->RenderedSignalImage:
  if self._state!="open":raise RenderingLifecycleError("initial image service is closing")
  operation=asyncio.current_task();self._active_tasks.add(operation)
  reservation=None;deferred=False
  try:
   template=await self.template_service.resolve(exchange,destination_id)
   reserve = self._reserve_output if output_prefix == "initial" else lambda: self._reserve_output_with_prefix(output_prefix)
   reservation_task=self._track_owned(asyncio.to_thread(reserve))
   try:reservation=await asyncio.shield(reservation_task)
   except asyncio.CancelledError:
    self._track_owned(self._finish_cancelled_reservation(reservation_task));deferred=True
    raise
   async with self._semaphore:
    payload=await asyncio.to_thread(payload_builder)
    worker=asyncio.create_task(_run_render_worker(payload,template.path,reservation.path))
    shielded=asyncio.shield(worker);shielded.add_done_callback(_observe_future)
    try:
     succeeded,result=await shielded
     if succeeded:
      reservation.transfer();return result
     raise result
    except asyncio.CancelledError:
     reservation.defer();deferred=True
     self._track_owned(self._finish_cancelled_worker(worker,reservation))
     raise
  except asyncio.CancelledError:
   if reservation is not None and not deferred:
    reservation.defer();self._track_owned(self._cleanup_reservation(reservation))
   raise
  except Exception:
   if reservation is not None:await self._cleanup_reservation(reservation)
   raise
  finally:self._active_tasks.discard(operation)
 async def _finish_cancelled_reservation(self,reservation_task):
  try:reservation=await reservation_task
  except BaseException:return
  reservation.defer();await self._cleanup_reservation(reservation)
 async def _finish_cancelled_worker(self,worker,reservation):
  try:await worker
  except BaseException:pass
  await self._cleanup_reservation(reservation)
 async def _cleanup_reservation(self,reservation):
  if reservation.transferred or reservation.cleaned:return
  await self._safe_cleanup(reservation.path);reservation.mark_cleaned()
 async def _safe_cleanup(self,output):
  try:await asyncio.to_thread(self.cleanup,output)
  except Exception as exc:LOGGER.warning("render cleanup failed category=%s",type(exc).__name__)
 async def drain_pending_cleanup(self):
  """Wait for all reservation and worker supervisors; safe to call repeatedly."""
  while self._owned_tasks:
   tasks=tuple(self._owned_tasks)
   await asyncio.gather(*(asyncio.shield(task) for task in tasks),return_exceptions=True)
 async def aclose(self):
  if self._close_task is None:
   self._state="closing";self._close_task=asyncio.create_task(self._close_owned_work())
  await asyncio.shield(self._close_task)
 async def _close_owned_work(self):
  while self._active_tasks:
   await asyncio.gather(*(asyncio.shield(task) for task in tuple(self._active_tasks)),return_exceptions=True)
  await self.drain_pending_cleanup();self._state="closed"
 def cleanup(self,path:Path|str):
  candidate=Path(path).resolve()
  if not candidate.is_relative_to(self.output_root) or candidate==self.output_root:raise UnsafeRenderPathError("render cleanup path is outside output root")
  try:
   candidate.unlink(missing_ok=True)
   for temporary in candidate.parent.glob(f".{candidate.name}.*.tmp"):temporary.unlink(missing_ok=True)
  except OSError as exc:raise RenderingError("render output cleanup failed") from exc

class UpdatePayloadFactory(InitialPayloadFactory):
 def build_update(self,signal,event,exchange,rendered_at=None):
  from chainura.domain.monitoring import SignalEventKind
  from chainura.rendering.models import SignalRenderEventKind
  if rendered_at is None:rendered_at=event.occurred_at
  mappings=[symbol for mapped,symbol in signal.exchange_symbols if mapped is exchange]
  if len(mappings)!=1:raise RenderPayloadError("exchange has no unique native-symbol mapping")
  logo=self.logo_store.resolve(exchange,mappings[0],signal.canonical_symbol) if exchange is Exchange.TOOBIT else None
  # Monitoring has already persisted the exact execution basis and matching
  # Decimal ROI. Event-time exchange prices remain evidence, not execution.
  event_price=event.roi_basis_price
  pnl=event.leveraged_roi
  kind={SignalEventKind.TP_HIT:SignalRenderEventKind.TP,SignalEventKind.SL_HIT:SignalRenderEventKind.SL,SignalEventKind.BREAKEVEN:SignalRenderEventKind.BREAKEVEN}[event.kind]
  elapsed=max(0,int((event.occurred_at-signal.created_at).total_seconds()))
  return SignalRenderPayload(signal.id,kind,exchange,signal.direction,signal.price_source_type,
   signal.canonical_symbol,mappings[0],signal.base_asset,signal.quote_asset,signal.entry_price,
   signal.leverage,signal.take_profits,signal.stop_loss,event.observed_reference_price,event_price,
   pnl,signal.created_at,event.occurred_at,elapsed,logo)
