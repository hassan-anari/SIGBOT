from __future__ import annotations
import asyncio, logging
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from sqlalchemy import select
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile
from chainura.application.initial_caption import build_signal_details_text
from chainura.application.message_templates import initial_context, details_context, event_context
from chainura.domain.message_template import DestinationMessageKind
from chainura.domain.destination import DestinationStatus
from chainura.domain.publication import PublicationStatus, tehran_local_date
from chainura.errors import (CaptionGenerationError, DailyCapacityError, TemplateAssetError,
 TemplateNotConfiguredError, TelegramDeliveryError, RenderPayloadError, RenderingError)
LOGGER=logging.getLogger("chainura.publication")

class DeliveryRejected(TelegramDeliveryError, RenderPayloadError, RenderingError):pass
class DeliveryAmbiguous(TelegramDeliveryError, RenderPayloadError, RenderingError):pass
class DeliveryRateLimited(TelegramDeliveryError, RenderPayloadError, RenderingError):
 def __init__(self,retry_after):self.retry_after=retry_after

class UpdateContentType(str, Enum):
 PHOTO="photo"
 TEXT="text"

def update_content_type(event_kind):
 from chainura.domain.monitoring import SignalEventKind
 mapping={SignalEventKind.TP_HIT:UpdateContentType.PHOTO,
  SignalEventKind.SL_HIT:UpdateContentType.TEXT,
  SignalEventKind.BREAKEVEN:UpdateContentType.PHOTO}
 try:return mapping[event_kind]
 except KeyError as exc:raise ValueError("unsupported update event kind") from exc

class TelegramPhotoPublisher:
 def __init__(self,bot):self.bot=bot
 async def send_initial(self,chat_id,path,caption):
  try:message=await self.bot.send_photo(chat_id=chat_id,photo=FSInputFile(path),caption=caption or None)
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
 async def send_text_update(self,chat_id,text,reply_to_message_id):
  try:message=await self.bot.send_message(chat_id=chat_id,text=text,reply_to_message_id=reply_to_message_id)
  except TelegramRetryAfter as exc:raise DeliveryRateLimited(max(0.0,float(exc.retry_after))) from None
  except (TelegramForbiddenError,TelegramBadRequest) as exc:raise DeliveryRejected(type(exc).__name__) from None
  except (TelegramNetworkError,TimeoutError,ConnectionError,asyncio.TimeoutError) as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  except Exception as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  message_id=getattr(message,'message_id',None)
  if isinstance(message_id,bool) or not isinstance(message_id,int) or message_id<=0:raise DeliveryAmbiguous('invalid_response')
  return message_id
 async def send_details(self,chat_id,text,reply_to_message_id):
  try:message=await self.bot.send_message(chat_id=chat_id,text=text,reply_to_message_id=reply_to_message_id)
  except TelegramRetryAfter as exc:raise DeliveryRateLimited(max(0.0,float(exc.retry_after))) from None
  except (TelegramForbiddenError,TelegramBadRequest) as exc:raise DeliveryRejected(type(exc).__name__) from None
  except (TelegramNetworkError,TimeoutError,ConnectionError,asyncio.TimeoutError) as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  except Exception as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  message_id=getattr(message,"message_id",None)
  if isinstance(message_id,bool) or not isinstance(message_id,int) or message_id<=0:raise DeliveryAmbiguous("invalid_response")
  return message_id
 async def send_standalone_text(self,chat_id,text):
  try:message=await self.bot.send_message(chat_id=chat_id,text=text)
  except TelegramRetryAfter as exc:raise DeliveryRateLimited(max(0.0,float(exc.retry_after))) from None
  except (TelegramForbiddenError,TelegramBadRequest) as exc:raise DeliveryRejected(type(exc).__name__) from None
  except (TelegramNetworkError,TimeoutError,ConnectionError,asyncio.TimeoutError) as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  except Exception as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  message_id=getattr(message,"message_id",None)
  if isinstance(message_id,bool) or not isinstance(message_id,int) or message_id<=0:raise DeliveryAmbiguous("invalid_response")
  return message_id
 async def send_initial_status(self,telegram_user_id,text,reply_markup):
  try:message=await self.bot.send_message(chat_id=telegram_user_id,text=text,reply_markup=reply_markup)
  except (TelegramForbiddenError,TelegramBadRequest) as exc:raise DeliveryRejected(type(exc).__name__) from None
  except Exception as exc:raise DeliveryAmbiguous(type(exc).__name__) from None
  message_id=getattr(message,"message_id",None)
  if isinstance(message_id,bool) or not isinstance(message_id,int) or message_id<=0:raise DeliveryAmbiguous("invalid_response")
  return message_id

class SignalDetailsPublicationService:
 """Durable text delivery; the stored initial-photo id is the reply parent."""
 def __init__(self,transactions,publisher,clock=lambda:datetime.now(timezone.utc),max_attempts=3,retry_base_seconds=.5,message_templates=None):
  self.transactions=transactions;self.publisher=publisher;self.clock=clock;self.max_attempts=max_attempts;self.retry_base_seconds=retry_base_seconds;self.message_templates=message_templates
 async def recover(self):
  from sqlalchemy import update
  from chainura.persistence.models import SignalDetailsPublicationRecord
  async with self.transactions() as tx:
   await tx._session.execute(update(SignalDetailsPublicationRecord).where(SignalDetailsPublicationRecord.status==PublicationStatus.SENDING).values(status=PublicationStatus.DELIVERY_UNKNOWN,last_error_category="restart_during_send",updated_at=self.clock()))
   await tx.commit()
 async def pending_ids(self):
  from sqlalchemy import select,or_
  from chainura.persistence.models import SignalDetailsPublicationRecord
  now=self.clock()
  async with self.transactions() as tx:
   ids=tuple((await tx._session.scalars(select(SignalDetailsPublicationRecord.id).where(or_(
    SignalDetailsPublicationRecord.status==PublicationStatus.PENDING,
    (SignalDetailsPublicationRecord.status==PublicationStatus.RETRY_WAIT)&(SignalDetailsPublicationRecord.retry_at<=now))).order_by(SignalDetailsPublicationRecord.id))).all());await tx.commit();return ids
 async def process(self,item_id):
  from sqlalchemy import update
  from chainura.persistence.models import SignalDetailsPublicationRecord,DestinationRecord
  now=self.clock()
  async with self.transactions() as tx:
   from chainura.persistence.models import SignalRecord
   from chainura.domain.signal import SignalLifecycleStatus
   claimed=(await tx._session.execute(update(SignalDetailsPublicationRecord).where(SignalDetailsPublicationRecord.id==item_id,SignalDetailsPublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RETRY_WAIT)),SignalDetailsPublicationRecord.signal_id.in_(select(SignalRecord.id).where(SignalRecord.status==SignalLifecycleStatus.ACTIVE))).values(status=PublicationStatus.SENDING,attempt_count=SignalDetailsPublicationRecord.attempt_count+1,first_attempt_at=now,updated_at=now,retry_at=None).returning(SignalDetailsPublicationRecord.signal_id,SignalDetailsPublicationRecord.destination_id,SignalDetailsPublicationRecord.reply_to_message_id,SignalDetailsPublicationRecord.attempt_count))).one_or_none()
   if claimed is None:return
   signal_id,destination_id,parent,attempts=claimed
   signal=await tx.signals.get_by_id(signal_id);destination=await tx._session.get(DestinationRecord,destination_id);await tx.commit()
  try:
   if signal is None or destination is None:raise RenderPayloadError("signal details publication data is missing")
   private=destination.access_level.value=="private"
   if self.message_templates:
    text=(await self.message_templates.resolve(DestinationMessageKind.SIGNAL_DETAILS,destination_id=destination_id,context=details_context(signal,private=private))).text
   else:text=build_signal_details_text(signal,private=private)
  except asyncio.CancelledError:raise
  except Exception as exc:
   LOGGER.warning("signal details preparation failed delivery_id=%s category=%s",item_id,type(exc).__name__)
   await self._finish(item_id,PublicationStatus.FAILED,type(exc).__name__)
   return
  try:message_id=await self.publisher.send_details(destination.telegram_chat_id,text,parent)
  except DeliveryRateLimited as exc:
   target=PublicationStatus.RETRY_WAIT if attempts<self.max_attempts else PublicationStatus.FAILED
   retry=self.clock()+timedelta(seconds=max(exc.retry_after,self.retry_base_seconds*(2**(attempts-1)))) if target is PublicationStatus.RETRY_WAIT else None
   return await self._finish(item_id,target,"rate_limited",retry_at=retry)
  except DeliveryRejected as exc:return await self._finish(item_id,PublicationStatus.FAILED,str(exc))
  except DeliveryAmbiguous as exc:return await self._finish(item_id,PublicationStatus.DELIVERY_UNKNOWN,str(exc))
  return await self._finish(item_id,PublicationStatus.SENT,message_id=message_id)
 async def recover_unexpected_failure(self,item_id):
  """Release ambiguous worker ownership after an uncaught processing failure."""
  from sqlalchemy import update
  from chainura.persistence.models import SignalDetailsPublicationRecord
  async with self.transactions() as tx:
   await tx._session.execute(update(SignalDetailsPublicationRecord).where(
    SignalDetailsPublicationRecord.id==item_id,
    SignalDetailsPublicationRecord.status==PublicationStatus.SENDING).values(
    status=PublicationStatus.DELIVERY_UNKNOWN,last_error_category="unexpected_processing_failure",
    updated_at=self.clock()))
   await tx.commit()
 async def _finish(self,item_id,target,error=None,message_id=None,retry_at=None):
  from sqlalchemy import update
  from chainura.persistence.models import SignalDetailsPublicationRecord
  values=dict(status=target,last_error_category=error,retry_at=retry_at,updated_at=self.clock())
  if target is PublicationStatus.SENT:values.update(telegram_message_id=message_id,sent_at=self.clock())
  async with self.transactions() as tx:
   await tx._session.execute(update(SignalDetailsPublicationRecord).where(SignalDetailsPublicationRecord.id==item_id,SignalDetailsPublicationRecord.status==PublicationStatus.SENDING).values(**values));await tx.commit()

class InitialPublicationService:
 def __init__(self,transactions,image_service,publisher,clock=lambda:datetime.now(timezone.utc),max_attempts=3,retry_base_seconds=.5,message_templates=None):
  self.transactions=transactions;self.image_service=image_service;self.publisher=publisher;self.clock=clock
  self.max_attempts=max_attempts;self.retry_base_seconds=retry_base_seconds;self.message_templates=message_templates
 async def recover(self):
  from sqlalchemy import update
  from chainura.persistence.models import InitialPublicationNotificationRecord
  async with self.transactions() as tx:
   await tx.publications.recover(self.clock())
   # Telegram may have accepted an interrupted send, so a retry can duplicate only
   # at this crash boundary. Re-queueing is preferable to permanently stranding a
   # durable PENDING_DETAILS signal without its continuation action.
   await tx._session.execute(update(InitialPublicationNotificationRecord).where(
    InitialPublicationNotificationRecord.status=="sending").values(
    status="pending",updated_at=self.clock()))
   await tx.commit()
 async def plan_pending(self):
  async with self.transactions() as tx:ids=await tx.publications.pending_request_ids();await tx.commit()
  for request_id in ids:
   async with self.transactions() as tx:await tx.publications.plan(request_id,self.clock());await tx.commit()
 async def pending_ids(self):
  async with self.transactions() as tx:ids=await tx.publications.pending_delivery_ids(self.clock());await tx.commit();return ids
 async def notify_completed(self):
  from sqlalchemy import select,update
  from chainura.persistence.models import InitialPublicationNotificationRecord,SignalRecord,SignalistRecord
  from chainura.telegram.presentation import texts
  from chainura.telegram.presentation.keyboards import signal_continue_details
  from chainura.trade_id import format_trade_id
  async with self.transactions() as tx:
   row=(await tx._session.scalars(select(InitialPublicationNotificationRecord).where(InitialPublicationNotificationRecord.status=="pending").order_by(InitialPublicationNotificationRecord.id).limit(1))).one_or_none()
   if row is None:return False
   claimed=(await tx._session.execute(update(InitialPublicationNotificationRecord).where(InitialPublicationNotificationRecord.id==row.id,InitialPublicationNotificationRecord.status=="pending").values(status="sending",updated_at=self.clock()))).rowcount
   signal=await tx._session.get(SignalRecord,row.signal_id);actor=await tx._session.get(SignalistRecord,signal.signalist_id) if signal else None;await tx.commit()
  if not claimed or actor is None:return False
  trade_id=format_trade_id(row.signal_id)
  copy=(texts.signal_initial_published(trade_id) if row.outcome=="success"
        else texts.signal_initial_partial(trade_id))
  try:message_id=await self.publisher.send_initial_status(actor.telegram_user_id,copy,signal_continue_details(row.signal_id))
  except Exception:
   # Delivery after intent is ambiguous and is deliberately never auto-resent.
   target="delivery_unknown";message_id=None
  else:target="sent"
  async with self.transactions() as tx:
   values={"status":target,"updated_at":self.clock()}
   if target=="sent":values.update(telegram_message_id=message_id,sent_at=self.clock())
   await tx._session.execute(update(InitialPublicationNotificationRecord).where(InitialPublicationNotificationRecord.id==row.id,InitialPublicationNotificationRecord.status=="sending").values(**values));await tx.commit()
  return True
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
   if self.message_templates:
    caption=(await self.message_templates.resolve(DestinationMessageKind.INITIAL_PHOTO_CAPTION,destination_id=delivery.destination_id,context=initial_context(signal,delivery.selected_exchange,private=delivery.private))).text
   else:caption=None
   caption=caption or None
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
    if hasattr(self.service,"notify_completed"):
     while await self.service.notify_completed():pass
   except asyncio.CancelledError:raise
   except Exception as exc:LOGGER.error("publication sweep failed category=%s",type(exc).__name__)
   if self._stop.is_set():break
   try:await asyncio.wait_for(self._wake.wait(),self.sweep_seconds)
   except asyncio.TimeoutError:pass
 async def close(self):
  self._stop.set();self._wake.set()
  if self._task is not None:await self._task

class UpdatePublicationService:
 def __init__(self,transactions,image_service,publisher,clock=lambda:datetime.now(timezone.utc),max_attempts=3,retry_base_seconds=.5,message_templates=None):
  self.transactions=transactions;self.image_service=image_service;self.publisher=publisher;self.clock=clock;self.max_attempts=max_attempts;self.retry_base_seconds=retry_base_seconds;self.message_templates=message_templates
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
    models=__import__('chainura.persistence.models',fromlist=['SignalEventRecord','DestinationRecord'])
    event=await tx._session.get(models.SignalEventRecord,delivery.event_id)
    destination=await tx._session.get(models.DestinationRecord,delivery.destination_id)
    await tx.commit()
   if event is None:raise RenderPayloadError("update event snapshot is inconsistent")
   if destination is None:raise RenderPayloadError("update destination is missing")
   if self.message_templates:
    kind={__import__('chainura.domain.monitoring',fromlist=['SignalEventKind']).SignalEventKind.TP_HIT:DestinationMessageKind.TP_HIT,
          __import__('chainura.domain.monitoring',fromlist=['SignalEventKind']).SignalEventKind.SL_HIT:DestinationMessageKind.SL_HIT,
          __import__('chainura.domain.monitoring',fromlist=['SignalEventKind']).SignalEventKind.BREAKEVEN:DestinationMessageKind.BREAKEVEN}[event.kind]
    caption=(await self.message_templates.resolve(kind,destination_id=delivery.destination_id,
      context=event_context(signal,event,delivery.selected_exchange,private=destination.access_level.value=="private"))).text
   else:
    from chainura.application.update_caption import UpdateCaptionInput, build_update_caption
    caption=build_update_caption(UpdateCaptionInput(event.kind,signal.canonical_symbol,event.observed_reference_price,event.leveraged_roi,event.tp_index))
   content_type=update_content_type(event.kind)
   if content_type is UpdateContentType.PHOTO:
    output=await self.image_service.render_update_signal(signal,event,delivery.selected_exchange,delivery.destination_id)
   async with self.transactions() as tx:
    state,parent=await tx.update_publications.prepare_send(delivery.id,self.clock());await tx.update_publications.complete_request(delivery.request_id,self.clock());await tx.commit()
   if state!='sending':return
   try:
    if content_type is UpdateContentType.TEXT:
     message_id=await self.publisher.send_text_update(delivery.telegram_chat_id,caption,parent)
    else:
     message_id=await self.publisher.send_update(delivery.telegram_chat_id,output.file_path,caption,parent)
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

class SignalDetailsPublicationWorker(InitialPublicationWorker):
 async def _run(self):
  await self.service.recover()
  while not self._stop.is_set():
   self._wake.clear()
   try:
    ids=await self.service.pending_ids()
    for offset in range(0,len(ids),self.concurrency):
     batch=ids[offset:offset+self.concurrency]
     results=await asyncio.gather(*(self.service.process(item) for item in batch),return_exceptions=True)
     for delivery_id,result in zip(batch,results):
      if isinstance(result,BaseException):
       LOGGER.warning("signal details delivery isolated delivery_id=%s category=%s",delivery_id,type(result).__name__)
       try:await self.service.recover_unexpected_failure(delivery_id)
       except Exception as recovery_exc:LOGGER.error("signal details ownership recovery failed delivery_id=%s category=%s",delivery_id,type(recovery_exc).__name__)
   except asyncio.CancelledError:raise
   except Exception as exc:LOGGER.error("signal details publication sweep failed category=%s",type(exc).__name__)
   if self._stop.is_set():break
   try:await asyncio.wait_for(self._wake.wait(),self.sweep_seconds)
   except asyncio.TimeoutError:pass

class CancellationPublicationService:
 """TEXT-only durable cancellation delivery; never touches the image pipeline."""
 def __init__(self,transactions,publisher,clock=lambda:datetime.now(timezone.utc),max_attempts=3,retry_base_seconds=2,message_templates=None):self.transactions=transactions;self.publisher=publisher;self.clock=clock;self.max_attempts=max_attempts;self.retry_base_seconds=retry_base_seconds;self.message_templates=message_templates
 async def recover(self):
  from sqlalchemy import update
  from chainura.persistence.models import SignalCancellationPublicationRecord
  async with self.transactions() as tx:
   await tx._session.execute(update(SignalCancellationPublicationRecord).where(SignalCancellationPublicationRecord.status==PublicationStatus.SENDING).values(status=PublicationStatus.DELIVERY_UNKNOWN,last_error_category="restart_during_send",updated_at=self.clock()));await tx.commit()
 async def recover_unexpected_failure(self,item_id):
  """Match worker isolation: an uncaught in-flight send is ambiguous, not retryable."""
  from sqlalchemy import update
  from chainura.persistence.models import SignalCancellationPublicationRecord
  async with self.transactions() as tx:
   await tx._session.execute(update(SignalCancellationPublicationRecord).where(
    SignalCancellationPublicationRecord.id==item_id,
    SignalCancellationPublicationRecord.status==PublicationStatus.SENDING).values(
    status=PublicationStatus.DELIVERY_UNKNOWN,last_error_category="unexpected_processing_failure",
    updated_at=self.clock()))
   await tx.commit()
 async def pending_ids(self):
  from sqlalchemy import or_
  from chainura.persistence.models import SignalCancellationPublicationRecord,SignalRecord,DestinationPublicationRecord
  from chainura.domain.signal import SignalLifecycleStatus
  now=self.clock()
  async with self.transactions() as tx:
   s=tx._session
   missing=(await s.execute(select(DestinationPublicationRecord.signal_id,DestinationPublicationRecord.destination_id).join(SignalRecord,SignalRecord.id==DestinationPublicationRecord.signal_id).where(SignalRecord.status==SignalLifecycleStatus.CANCELLED,DestinationPublicationRecord.status==PublicationStatus.SENT,DestinationPublicationRecord.telegram_message_id>0,~DestinationPublicationRecord.destination_id.in_(select(SignalCancellationPublicationRecord.destination_id).where(SignalCancellationPublicationRecord.signal_id==DestinationPublicationRecord.signal_id))))).all()
   for signal_id,destination_id in missing:s.add(SignalCancellationPublicationRecord(signal_id=signal_id,destination_id=destination_id,status=PublicationStatus.PENDING,created_at=now,updated_at=now))
   await s.flush();ids=tuple((await s.scalars(select(SignalCancellationPublicationRecord.id).where(or_(SignalCancellationPublicationRecord.status==PublicationStatus.PENDING,(SignalCancellationPublicationRecord.status==PublicationStatus.RETRY_WAIT)&(SignalCancellationPublicationRecord.retry_at<=now))).order_by(SignalCancellationPublicationRecord.id))).all());await tx.commit();return ids
 async def process(self,item_id):
  from sqlalchemy import exists,update
  from chainura.persistence.models import (SignalCancellationPublicationRecord,SignalRecord,DestinationRecord,DestinationSignalistRecord,DestinationPublicationRecord,SignalDetailsPublicationRecord,DestinationUpdatePublicationRecord,SignalCommentPublicationRecord)
  from chainura.domain.signal import SignalLifecycleStatus
  now=self.clock()
  async with self.transactions() as tx:
   s=tx._session;row=await s.get(SignalCancellationPublicationRecord,item_id)
   if not row or row.status not in (PublicationStatus.PENDING,PublicationStatus.RETRY_WAIT):return
   # Do not overtake a Telegram call whose result is still genuinely unknown.
   details_sending=await s.scalar(select(exists().where(SignalDetailsPublicationRecord.signal_id==row.signal_id,SignalDetailsPublicationRecord.destination_id==row.destination_id,SignalDetailsPublicationRecord.status==PublicationStatus.SENDING)))
   update_sending=await s.scalar(select(exists().where(DestinationUpdatePublicationRecord.signal_id==row.signal_id,DestinationUpdatePublicationRecord.destination_id==row.destination_id,DestinationUpdatePublicationRecord.status==PublicationStatus.SENDING)))
   comment_sending=await s.scalar(select(exists().where(SignalCommentPublicationRecord.signal_id==row.signal_id,SignalCommentPublicationRecord.destination_id==row.destination_id,SignalCommentPublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.SENDING,PublicationStatus.RETRY_WAIT,PublicationStatus.DELIVERY_UNKNOWN)))))
   inflight=details_sending or update_sending or comment_sending
   if inflight:return
   signal=await tx.signals.get_by_id(row.signal_id);dest=await s.get(DestinationRecord,row.destination_id)
   assigned=signal and await s.scalar(select(exists().where(DestinationSignalistRecord.destination_id==row.destination_id,DestinationSignalistRecord.signalist_id==signal.signalist_id)));
   initial=await s.scalar(select(DestinationPublicationRecord.telegram_message_id).where(DestinationPublicationRecord.signal_id==row.signal_id,DestinationPublicationRecord.destination_id==row.destination_id,DestinationPublicationRecord.status==PublicationStatus.SENT,DestinationPublicationRecord.telegram_message_id>0))
   if not signal or signal.status is not SignalLifecycleStatus.CANCELLED or not dest or dest.status is not DestinationStatus.ACTIVE or not assigned or not initial:
    row.status=PublicationStatus.SUPERSEDED;row.updated_at=now;await tx.commit();return
   parent=await tx.update_publications.last_published_message_id(row.signal_id,row.destination_id)
   row.status=PublicationStatus.SENDING;row.reply_to_message_id=parent;row.attempt_count+=1;row.first_attempt_at=row.first_attempt_at or now;row.retry_at=None;row.updated_at=now;attempt=row.attempt_count;chat=dest.telegram_chat_id;did=row.destination_id;await tx.commit()
  try:
   text=(await self.message_templates.resolve(DestinationMessageKind.SIGNAL_CANCELLED,destination_id=did,context=__import__('chainura.application.message_templates',fromlist=['cancellation_context']).cancellation_context(signal,private=dest.access_level.value=="private"))).text
   message_id=await self.publisher.send_text_update(chat,text,parent)
  except DeliveryRateLimited as exc:
   target=PublicationStatus.RETRY_WAIT if attempt<self.max_attempts else PublicationStatus.FAILED;retry=self.clock()+timedelta(seconds=max(exc.retry_after,self.retry_base_seconds*(2**(attempt-1)))) if target is PublicationStatus.RETRY_WAIT else None
   return await self._finish(item_id,target,"rate_limited",retry)
  except DeliveryRejected as exc:return await self._finish(item_id,PublicationStatus.FAILED,str(exc))
  except DeliveryAmbiguous as exc:return await self._finish(item_id,PublicationStatus.DELIVERY_UNKNOWN,str(exc))
  except Exception as exc:return await self._finish(item_id,PublicationStatus.FAILED,type(exc).__name__)
  await self._finish(item_id,PublicationStatus.SENT,message_id=message_id)
 async def _finish(self,item_id,target,error=None,retry=None,message_id=None):
  from chainura.persistence.models import SignalCancellationPublicationRecord
  async with self.transactions() as tx:
   row=await tx._session.get(SignalCancellationPublicationRecord,item_id)
   if row and row.status is PublicationStatus.SENDING:
    row.status=target;row.last_error_category=error;row.retry_at=retry;row.updated_at=self.clock()
    if target is PublicationStatus.SENT:row.telegram_message_id=message_id;row.sent_at=self.clock()
   await tx.commit()

class CancellationPublicationWorker(SignalDetailsPublicationWorker):pass
