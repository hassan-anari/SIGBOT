from __future__ import annotations
import asyncio, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile
from chainura.application.initial_caption import build_initial_caption
from chainura.domain.destination import DestinationStatus
from chainura.domain.publication import PublicationStatus, tehran_local_date
from chainura.errors import (CaptionGenerationError, DailyCapacityError, TemplateAssetError,
 TemplateNotConfiguredError, TelegramDeliveryError, RenderPayloadError, RenderingError)
LOGGER=logging.getLogger("chainura.publication")

class DeliveryRejected(TelegramDeliveryError, RenderPayloadError, RenderingError):pass
class DeliveryAmbiguous(TelegramDeliveryError, RenderPayloadError, RenderingError):pass
class DeliveryRateLimited(TelegramDeliveryError, RenderPayloadError, RenderingError):
 def __init__(self,retry_after):self.retry_after=retry_after

class TelegramPhotoPublisher:
 def __init__(self,bot):self.bot=bot
 async def send_initial(self,chat_id,path,caption):
  try:message=await self.bot.send_photo(chat_id=chat_id,photo=FSInputFile(path),caption=caption)
  except TelegramRetryAfter as exc:raise DeliveryRateLimited(max(0.0,float(exc.retry_after))) from None
  except (TelegramForbiddenError,TelegramBadRequest) as exc:raise DeliveryRejected(type(exc).__name__) from None
  except (TelegramNetworkError,TimeoutError,ConnectionError,asyncio.TimeoutError) as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  except Exception as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  message_id=getattr(message,"message_id",None)
  if isinstance(message_id,bool) or not isinstance(message_id,int) or message_id<=0:raise DeliveryAmbiguous("invalid_response")
  return message_id
 async def send_update(self,chat_id,path,caption,reply_to_message_id):
  try:message=await self.bot.send_photo(chat_id=chat_id,photo=FSInputFile(path),caption=caption,reply_to_message_id=reply_to_message_id)
  except TelegramRetryAfter as exc:raise DeliveryRateLimited(max(0.0,float(exc.retry_after))) from None
  except (TelegramForbiddenError,TelegramBadRequest) as exc:raise DeliveryRejected(type(exc).__name__) from None
  except (TelegramNetworkError,TimeoutError,ConnectionError,asyncio.TimeoutError) as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  except Exception as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  message_id=getattr(message,'message_id',None)
  if isinstance(message_id,bool) or not isinstance(message_id,int) or message_id<=0:raise DeliveryAmbiguous('invalid_response')
  return message_id

class InitialPublicationService:
 def __init__(self,transactions,image_service,publisher,clock=lambda:datetime.now(timezone.utc),max_attempts=3,retry_base_seconds=.5):
  self.transactions=transactions;self.image_service=image_service;self.publisher=publisher;self.clock=clock
  self.max_attempts=max_attempts;self.retry_base_seconds=retry_base_seconds
 async def recover(self):
  async with self.transactions() as tx:await tx.publications.recover(self.clock());await tx.commit()
 async def plan_pending(self):
  async with self.transactions() as tx:ids=await tx.publications.pending_request_ids();await tx.commit()
  for request_id in ids:
   async with self.transactions() as tx:await tx.publications.plan(request_id,self.clock());await tx.commit()
 async def pending_ids(self):
  async with self.transactions() as tx:ids=await tx.publications.pending_delivery_ids(self.clock());await tx.commit();return ids
 async def unblock_templates(self):
  async with self.transactions() as tx:await tx.publications.unblock_templates(self.clock());await tx.commit()
 async def process(self,publication_id):
  now=self.clock()
  async with self.transactions() as tx:
   claimed=await tx.publications.claim(publication_id,now);await tx.commit()
  if claimed is None:return
  delivery,status,assigned=claimed
  if status is not DestinationStatus.ACTIVE or not assigned:
   target=PublicationStatus.SKIPPED_INACTIVE if status is not DestinationStatus.ACTIVE else PublicationStatus.SKIPPED_UNASSIGNED
   await self._finish(delivery,target);return
  # Capacity is durable before any external operation. Conflicts are retried in bounded transactions.
  capacity=None
  for attempt in range(3):
   try:
    async with self.transactions() as tx:
     capacity=await tx.publications.claim_capacity(delivery.id,delivery.destination_id,
      tehran_local_date(now),delivery.daily_limit,now);await tx.commit()
    break
   except DailyCapacityError:
    if attempt==2:capacity=None
  if capacity is None:
   await self._finish(delivery,PublicationStatus.SKIPPED_DAILY_LIMIT);return
  output=None
  try:
   async with self.transactions() as tx:signal=await tx.signals.get_by_id(delivery.signal_id);await tx.commit()
   caption=build_initial_caption(signal,private=delivery.private)
   output=await self.image_service.render_initial_signal(signal,delivery.selected_exchange,delivery.destination_id,now)
   async with self.transactions() as tx:
    ownership=await tx.publications.prepare_send(delivery.id,delivery.signal_id,delivery.destination_id,self.clock())
    await tx.publications.complete_request(delivery.signal_id,self.clock());await tx.commit()
   if ownership!="sending":return
   try:message_id=await self.publisher.send_initial(delivery.telegram_chat_id,output.file_path,caption)
   except DeliveryRateLimited as exc:
    attempts=delivery.attempt_count+1
    if attempts<self.max_attempts:
     delay=max(exc.retry_after,self.retry_base_seconds*(2**(attempts-1)))
     retry_at=self.clock()+timedelta(seconds=delay)
     async with self.transactions() as tx:
      changed=await tx.publications.transition(delivery.id,PublicationStatus.SENDING,
       PublicationStatus.RETRY_WAIT,self.clock(),error="rate_limited",retry_at=retry_at)
      if not changed:raise TelegramDeliveryError("publication ownership was lost")
      await tx.commit()
     return
    await self._finish(delivery,PublicationStatus.FAILED,"rate_limit_exhausted",expected=PublicationStatus.SENDING,release=True);return
   except DeliveryRejected as exc:
    await self._finish(delivery,PublicationStatus.FAILED,str(exc),expected=PublicationStatus.SENDING,release=True);return
   except DeliveryAmbiguous as exc:
    await self._finish(delivery,PublicationStatus.DELIVERY_UNKNOWN,str(exc),expected=PublicationStatus.SENDING);return
   try:
    async with self.transactions() as tx:
     changed=await tx.publications.transition(delivery.id,PublicationStatus.SENDING,
      PublicationStatus.SENT,self.clock(),message_id=message_id)
     if not changed:raise TelegramDeliveryError("publication ownership was lost")
     await tx.publications.complete_request(delivery.signal_id,self.clock());await tx.commit()
   except Exception as exc:
    LOGGER.warning("sent result persistence ambiguous publication_id=%s category=%s",delivery.id,type(exc).__name__)
    try:await self._finish(delivery,PublicationStatus.DELIVERY_UNKNOWN,"sent_commit_failed",expected=PublicationStatus.SENDING)
    except Exception as recovery_exc:LOGGER.warning("ambiguous recovery failed publication_id=%s category=%s",delivery.id,type(recovery_exc).__name__)
  except TemplateNotConfiguredError:
   await self._finish(delivery,PublicationStatus.BLOCKED_TEMPLATE,"template_missing",release=True)
  except (TemplateAssetError,CaptionGenerationError) as exc:
   await self._finish(delivery,PublicationStatus.BLOCKED_TEMPLATE if isinstance(exc,TemplateAssetError) else PublicationStatus.FAILED,type(exc).__name__,release=True)
  except asyncio.CancelledError:raise
  except Exception as exc:
   LOGGER.warning("publication failed publication_id=%s category=%s",delivery.id,type(exc).__name__)
   await self._finish(delivery,PublicationStatus.FAILED,type(exc).__name__,release=True)
  finally:
   if output is not None:
    try:self.image_service.cleanup(output.file_path)
    except Exception as exc:LOGGER.warning("publication cleanup failed publication_id=%s category=%s",delivery.id,type(exc).__name__)
 async def _finish(self,delivery,target,error=None,*,expected=PublicationStatus.RENDERING,release=False):
  async with self.transactions() as tx:
   changed=await tx.publications.transition(delivery.id,expected,target,self.clock(),error=error)
   if not changed:return False
   if release:await tx.publications.release_capacity(delivery.id)
   await tx.publications.complete_request(delivery.signal_id,self.clock());await tx.commit()
   return True

class InitialPublicationWorker:
 def __init__(self,service,concurrency=3,sweep_seconds=5.0):
  if not 1<=concurrency<=20 or not 0.1<=sweep_seconds<=300:raise ValueError("invalid publication worker settings")
  self.service=service;self.concurrency=concurrency;self.sweep_seconds=sweep_seconds;self._wake=asyncio.Event();self._stop=asyncio.Event();self._task=None
 def wake(self):self._wake.set()
 async def start(self):
  if self._task is None:self._task=asyncio.create_task(self._run())
 @property
 def task(self):return self._task
 async def _run(self):
  while not self._stop.is_set():
   self._wake.clear()
   try:
    await self.service.plan_pending();ids=await self.service.pending_ids()
    for offset in range(0,len(ids),self.concurrency):
     batch=ids[offset:offset+self.concurrency]
     results=await asyncio.gather(*(self.service.process(item) for item in batch),return_exceptions=True)
     for publication_id,result in zip(batch,results):
      if isinstance(result,BaseException):LOGGER.warning("publication delivery isolated publication_id=%s category=%s",publication_id,type(result).__name__)
   except asyncio.CancelledError:raise
   except Exception as exc:LOGGER.error("publication sweep failed category=%s",type(exc).__name__)
   if self._stop.is_set():break
   try:await asyncio.wait_for(self._wake.wait(),self.sweep_seconds)
   except asyncio.TimeoutError:pass
 async def close(self):
  self._stop.set();self._wake.set()
  if self._task is not None:await self._task

class UpdatePublicationService:
 def __init__(self,transactions,image_service,publisher,clock=lambda:datetime.now(timezone.utc),max_attempts=3,retry_base_seconds=.5):
  self.transactions=transactions;self.image_service=image_service;self.publisher=publisher;self.clock=clock;self.max_attempts=max_attempts;self.retry_base_seconds=retry_base_seconds
 async def recover(self):
  async with self.transactions() as tx:await tx.update_publications.recover(self.clock());await tx.commit()
 async def sweep(self):
  async with self.transactions() as tx:
   now=self.clock();await tx.update_publications.reconcile_requests(now);await tx.update_publications.plan_pending(now);await tx.update_publications.supersede_obsolete_tp(now);await tx.commit()
 async def pending_ids(self):
  async with self.transactions() as tx:ids=await tx.update_publications.pending_delivery_ids(self.clock());await tx.commit();return ids
 async def unblock_templates(self):
  async with self.transactions() as tx:await tx.update_publications.unblock_templates(self.clock());await tx.commit()
 async def process(self,delivery_id):
  async with self.transactions() as tx:delivery=await tx.update_publications.claim(delivery_id,self.clock());await tx.commit()
  if delivery is None:return
  output=None
  try:
   async with self.transactions() as tx:
    signal=await tx.signals.get_by_id(delivery.signal_id)
    event=await tx._session.get(__import__('chainura.persistence.models',fromlist=['SignalEventRecord']).SignalEventRecord,delivery.event_id)
    price=await tx._session.get(__import__('chainura.persistence.models',fromlist=['SignalEventSourcePriceRecord']).SignalEventSourcePriceRecord,(delivery.event_id,delivery.selected_exchange))
    await tx.commit()
   if event is None or price is None:raise RenderPayloadError("update event snapshot is inconsistent")
   from chainura.application.update_caption import UpdateCaptionInput, build_update_caption, UpdateCaptionError
   caption=build_update_caption(UpdateCaptionInput(event.kind,signal.canonical_symbol,event.observed_reference_price,event.leveraged_roi,event.tp_index))
   output=await self.image_service.render_update_signal(signal,event,price.price,delivery.selected_exchange,delivery.destination_id)
   async with self.transactions() as tx:
    state,parent=await tx.update_publications.prepare_send(delivery.id,self.clock());await tx.update_publications.complete_request(delivery.request_id,self.clock());await tx.commit()
   if state!='sending':return
   try:message_id=await self.publisher.send_update(delivery.telegram_chat_id,output.file_path,caption,parent)
   except DeliveryRateLimited as exc:
    attempts=delivery.attempt_count+1
    if attempts<self.max_attempts:
     retry_at=self.clock()+timedelta(seconds=max(exc.retry_after,self.retry_base_seconds*(2**(attempts-1))))
     async with self.transactions() as tx:await tx.update_publications.transition(delivery.id,PublicationStatus.SENDING,PublicationStatus.RETRY_WAIT,self.clock(),error='rate_limited',retry_at=retry_at);await tx.commit()
     return
    await self._finish(delivery,PublicationStatus.FAILED,'rate_limit_exhausted',PublicationStatus.SENDING);return
   except DeliveryRejected as exc:await self._finish(delivery,PublicationStatus.FAILED,str(exc),PublicationStatus.SENDING);return
   except DeliveryAmbiguous as exc:await self._finish(delivery,PublicationStatus.DELIVERY_UNKNOWN,str(exc),PublicationStatus.SENDING);return
   try:
    async with self.transactions() as tx:await tx.update_publications.transition(delivery.id,PublicationStatus.SENDING,PublicationStatus.SENT,self.clock(),message_id=message_id);await tx.update_publications.complete_request(delivery.request_id,self.clock());await tx.commit()
   except Exception:
    await self._finish(delivery,PublicationStatus.DELIVERY_UNKNOWN,'sent_commit_failed',PublicationStatus.SENDING)
  except asyncio.CancelledError:raise
  except TemplateNotConfiguredError:await self._finish(delivery,PublicationStatus.BLOCKED_TEMPLATE,'template_missing')
  except TemplateAssetError:await self._finish(delivery,PublicationStatus.BLOCKED_TEMPLATE,'TemplateAssetError')
  except Exception as exc:
   safe=type(exc).__name__
   LOGGER.warning('update publication pre-send failed delivery_id=%s category=%s',delivery.id,safe)
   await self._finish(delivery,PublicationStatus.FAILED,safe)
  finally:
   if output is not None:
    try:self.image_service.cleanup(output.file_path)
    except Exception:pass
 async def _finish(self,delivery,target,error=None,expected=PublicationStatus.RENDERING):
  async with self.transactions() as tx:await tx.update_publications.transition(delivery.id,expected,target,self.clock(),error=error);await tx.update_publications.complete_request(delivery.request_id,self.clock());await tx.commit()

class UpdatePublicationWorker(InitialPublicationWorker):
 async def _run(self):
  while not self._stop.is_set():
   self._wake.clear()
   try:
    await self.service.sweep();ids=await self.service.pending_ids()
    for offset in range(0,len(ids),self.concurrency):
     batch=ids[offset:offset+self.concurrency]
     results=await asyncio.gather(*(self.service.process(item) for item in batch),return_exceptions=True)
     for delivery_id,result in zip(batch,results):
      if isinstance(result,BaseException):LOGGER.warning("update publication delivery isolated delivery_id=%s category=%s",delivery_id,type(result).__name__)
   except asyncio.CancelledError:raise
   except Exception as exc:LOGGER.error('update publication sweep failed category=%s',type(exc).__name__)
   if self._stop.is_set():break
   try:await asyncio.wait_for(self._wake.wait(),self.sweep_seconds)
   except asyncio.TimeoutError:pass
