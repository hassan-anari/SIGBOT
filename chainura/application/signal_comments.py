"""Durable manual comments for open Signals (no monitoring/event side effects)."""
from __future__ import annotations
import asyncio,json
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta
from html import escape
from types import SimpleNamespace
from sqlalchemy import exists,or_,select,update
from chainura.application.profit import leveraged_return_percent
from chainura.application.monitoring import MonitorWork
from chainura.application.publication import DeliveryAmbiguous,DeliveryRateLimited,DeliveryRejected
from chainura.domain.destination import DestinationStatus,Exchange
from chainura.domain.monitoring import MonitorStage
from chainura.domain.publication import PublicationStatus
from chainura.domain.signal import SignalLifecycleStatus
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.models import (DestinationExchangePriorityRecord,DestinationPublicationRecord,
 DestinationRecord,DestinationSignalistRecord,DestinationUpdatePublicationRecord,SignalCancellationPublicationRecord,
 SignalCommentPublicationRecord,SignalCommentRequestRecord,SignalDetailsPublicationRecord,SignalMonitorStateRecord,
 SignalRecord,SignalistRecord)

OPEN_STAGES=(MonitorStage.PRE_TP1,MonitorStage.POST_TP1)
@dataclass(frozen=True,slots=True)
class CommentCreationResult:
 created:bool;publication_count:int=0;reason:str|None=None;request_id:int|None=None

class SignalCommentService:
 def __init__(self,transactions,snapshot_provider=None,clock=lambda:datetime.now(timezone.utc),wake=None):
  self.transactions=transactions;self.snapshot_provider=snapshot_provider;self.clock=clock;self.wake=wake
 async def eligible(self,signal_id,signalist_id):
  async with self.transactions() as tx:return await self._eligible(tx._session,signal_id,signalist_id)
 async def _eligible(self,s,signal_id,signalist_id):
  actor=await s.get(SignalistRecord,signalist_id);signal=await s.get(SignalRecord,signal_id);state=await s.get(SignalMonitorStateRecord,signal_id)
  return bool(actor and actor.status is SignalistStatus.ACTIVE and signal and signal.signalist_id==signalist_id and signal.status is SignalLifecycleStatus.ACTIVE and state and state.stage in OPEN_STAGES)
 async def create(self,signal_id,signalist_id,comment_text,include_snapshot):
  text=comment_text if isinstance(comment_text,str) else ""
  if not text.strip() or len(text)>4096:return CommentCreationResult(False,reason="invalid_text")
  if include_snapshot and len(text)>1024:return CommentCreationResult(False,reason="caption_too_long")
  decision=None
  if include_snapshot:
   if self.snapshot_provider is None:return CommentCreationResult(False,reason="market_unavailable")
   async with self.transactions() as tx:
    if not await self._eligible(tx._session,signal_id,signalist_id):return CommentCreationResult(False,reason="stale")
    signal=await tx.signals.get_by_id(signal_id)
    work=MonitorWork(signal.id,signal.signalist_id,signal.mode,signal.canonical_symbol,
     signal.selected_exchange,signal.direction,signal.entry_price,signal.leverage,
     signal.take_profits,signal.stop_loss,signal.price_source_type,
     signal.exchange_symbols,signal.created_at)
   try:
    decision=(await self.snapshot_provider.snapshot((work,))).get(signal_id)
    if decision is None and getattr(self.snapshot_provider.catalog,"market_service",None) is not None:
     await self.snapshot_provider.catalog.market_service.refresh_prices(tuple(e for e,_ in signal.exchange_symbols))
     decision=(await self.snapshot_provider.snapshot((work,))).get(signal_id)
   except Exception:decision=None
   if decision is None:return CommentCreationResult(False,reason="market_unavailable")
  now=self.clock()
  async with self.transactions() as tx:
   s=tx._session
   if not await self._eligible(s,signal_id,signalist_id):return CommentCreationResult(False,reason="stale")
   sources={e.value:str(p) for e,p in decision.source_prices} if decision else {}
   request=SignalCommentRequestRecord(signal_id=signal_id,signalist_id=signalist_id,comment_text=text,include_snapshot=include_snapshot,
    snapshot_observed_at=decision.observed_at if decision else None,reference_price=decision.reference_price if decision else None,
    source_prices_json=json.dumps(sources,sort_keys=True,separators=(",",":")) if decision else None,created_at=now,updated_at=now)
   s.add(request);await s.flush()
   initials=(await s.execute(select(DestinationPublicationRecord.destination_id).join(DestinationRecord,DestinationRecord.id==DestinationPublicationRecord.destination_id).join(DestinationSignalistRecord,(DestinationSignalistRecord.destination_id==DestinationPublicationRecord.destination_id)&(DestinationSignalistRecord.signalist_id==signalist_id)).where(DestinationPublicationRecord.signal_id==signal_id,DestinationPublicationRecord.status==PublicationStatus.SENT,DestinationPublicationRecord.telegram_message_id>0,DestinationRecord.status==DestinationStatus.ACTIVE))).all()
   for (did,) in initials:
    selected=None
    if include_snapshot:
     priorities=(await s.scalars(select(DestinationExchangePriorityRecord.exchange).where(DestinationExchangePriorityRecord.destination_id==did).order_by(DestinationExchangePriorityRecord.position))).all()
     selected=next((e for e in priorities if e.value in sources),None)
     if selected is None:continue
    s.add(SignalCommentPublicationRecord(comment_request_id=request.id,signal_id=signal_id,destination_id=did,selected_exchange=selected,status=PublicationStatus.PENDING,created_at=now,updated_at=now))
   await s.flush();count=await s.scalar(select(__import__('sqlalchemy').func.count()).select_from(SignalCommentPublicationRecord).where(SignalCommentPublicationRecord.comment_request_id==request.id)) or 0
   if not count:return CommentCreationResult(False,reason="no_destinations")
   await tx.commit();rid=request.id
  if self.wake:self.wake()
  return CommentCreationResult(True,count,request_id=rid)

class SignalCommentPublicationService:
 def __init__(self,transactions,publisher,image_service=None,clock=lambda:datetime.now(timezone.utc),max_attempts=3,retry_base_seconds=2):
  self.transactions=transactions;self.publisher=publisher;self.image_service=image_service;self.clock=clock;self.max_attempts=max_attempts;self.retry_base_seconds=retry_base_seconds
 async def recover(self):
  async with self.transactions() as tx:
   await tx._session.execute(update(SignalCommentPublicationRecord).where(SignalCommentPublicationRecord.status==PublicationStatus.RENDERING).values(status=PublicationStatus.PENDING,last_error_category="restart_during_render",updated_at=self.clock()))
   await tx._session.execute(update(SignalCommentPublicationRecord).where(SignalCommentPublicationRecord.status==PublicationStatus.SENDING).values(status=PublicationStatus.DELIVERY_UNKNOWN,last_error_category="restart_during_send",updated_at=self.clock()));await tx.commit()
 async def pending_ids(self):
  now=self.clock()
  async with self.transactions() as tx:return tuple((await tx._session.scalars(select(SignalCommentPublicationRecord.id).where(or_(SignalCommentPublicationRecord.status==PublicationStatus.PENDING,(SignalCommentPublicationRecord.status==PublicationStatus.RETRY_WAIT)&(SignalCommentPublicationRecord.retry_at<=now))).order_by(SignalCommentPublicationRecord.id))).all())
 async def process(self,item_id):
  now=self.clock();output=None
  async with self.transactions() as tx:
   s=tx._session;row=await s.get(SignalCommentPublicationRecord,item_id)
   if not row or row.status not in (PublicationStatus.PENDING,PublicationStatus.RETRY_WAIT):return
   earlier=await s.scalar(select(exists().where(SignalCommentPublicationRecord.signal_id==row.signal_id,SignalCommentPublicationRecord.destination_id==row.destination_id,SignalCommentPublicationRecord.id<row.id,SignalCommentPublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.SENDING,PublicationStatus.RETRY_WAIT,PublicationStatus.DELIVERY_UNKNOWN)))))
   details_barrier=await s.scalar(select(exists().where(SignalDetailsPublicationRecord.signal_id==row.signal_id,SignalDetailsPublicationRecord.destination_id==row.destination_id,SignalDetailsPublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.RETRY_WAIT,PublicationStatus.SENDING,PublicationStatus.DELIVERY_UNKNOWN,PublicationStatus.BLOCKED_TEMPLATE)))))
   older_update=await s.scalar(select(exists().where(DestinationUpdatePublicationRecord.signal_id==row.signal_id,DestinationUpdatePublicationRecord.destination_id==row.destination_id,DestinationUpdatePublicationRecord.created_at<=row.created_at,DestinationUpdatePublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.READY,PublicationStatus.RETRY_WAIT,PublicationStatus.SENDING,PublicationStatus.DELIVERY_UNKNOWN,PublicationStatus.BLOCKED_TEMPLATE)))))
   cancellation_barrier=await s.scalar(select(exists().where(SignalCancellationPublicationRecord.signal_id==row.signal_id,SignalCancellationPublicationRecord.destination_id==row.destination_id,SignalCancellationPublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.RETRY_WAIT,PublicationStatus.SENDING,PublicationStatus.DELIVERY_UNKNOWN)))))
   foreign=details_barrier or older_update or cancellation_barrier
   req=await s.get(SignalCommentRequestRecord,row.comment_request_id);signal=await tx.signals.get_by_id(row.signal_id);dest=await s.get(DestinationRecord,row.destination_id);actor=await s.get(SignalistRecord,signal.signalist_id) if signal else None;state=await s.get(SignalMonitorStateRecord,row.signal_id)
   assigned=signal and await s.scalar(select(exists().where(DestinationSignalistRecord.destination_id==row.destination_id,DestinationSignalistRecord.signalist_id==signal.signalist_id)))
   valid=signal and signal.status is SignalLifecycleStatus.ACTIVE and state and state.stage in OPEN_STAGES and actor and actor.status is SignalistStatus.ACTIVE and dest and dest.status is DestinationStatus.ACTIVE and assigned
   if not valid:row.status=PublicationStatus.SUPERSEDED;row.updated_at=now;await tx.commit();return
   if earlier or foreign:return
   parent=await tx.update_publications.last_published_message_id(row.signal_id,row.destination_id)
   if not parent:row.status=PublicationStatus.SUPERSEDED;row.updated_at=now;await tx.commit();return
   row.status=PublicationStatus.RENDERING if req.include_snapshot else PublicationStatus.SENDING;row.reply_to_message_id=parent;row.attempt_count+=1;row.first_attempt_at=row.first_attempt_at or now;row.retry_at=None;row.updated_at=now
   attempt=row.attempt_count;chat=dest.telegram_chat_id;exchange=row.selected_exchange;await tx.commit()
  try:
   safe_text=escape(req.comment_text)
   if req.include_snapshot:
    if self.image_service is None:raise RuntimeError("snapshot renderer unavailable")
    prices={Exchange(k):__import__('decimal').Decimal(v) for k,v in json.loads(req.source_prices_json).items()}
    captured=SimpleNamespace(observed_reference_price=req.reference_price,roi_basis_price=req.reference_price,leveraged_roi=leveraged_return_percent(signal.entry_price,req.reference_price,signal.leverage,signal.direction),occurred_at=req.snapshot_observed_at)
    output=await self.image_service.render_snapshot_signal(signal,captured,exchange,row.destination_id)
    async with self.transactions() as tx:
     claimed=(await tx._session.execute(update(SignalCommentPublicationRecord).where(SignalCommentPublicationRecord.id==item_id,SignalCommentPublicationRecord.status==PublicationStatus.RENDERING).values(status=PublicationStatus.SENDING,updated_at=self.clock()))).rowcount;await tx.commit()
    if claimed!=1:return
    message_id=await self.publisher.send_update(chat,output.file_path,safe_text,parent)
   else:message_id=await self.publisher.send_text_update(chat,safe_text,parent)
  except DeliveryRateLimited as exc:
   target=PublicationStatus.RETRY_WAIT if attempt<self.max_attempts else PublicationStatus.FAILED;retry=self.clock()+timedelta(seconds=max(exc.retry_after,self.retry_base_seconds*2**(attempt-1))) if target is PublicationStatus.RETRY_WAIT else None;return await self._finish(item_id,target,"rate_limited",retry)
  except DeliveryRejected as exc:return await self._finish(item_id,PublicationStatus.FAILED,str(exc))
  except DeliveryAmbiguous as exc:return await self._finish(item_id,PublicationStatus.DELIVERY_UNKNOWN,str(exc))
  except asyncio.CancelledError:raise
  except Exception as exc:return await self._finish(item_id,PublicationStatus.FAILED,type(exc).__name__,expected=(PublicationStatus.RENDERING,PublicationStatus.SENDING))
  finally:
   if output is not None:
    try:self.image_service.cleanup(output.file_path)
    except Exception:pass
  await self._finish(item_id,PublicationStatus.SENT,message_id=message_id)
 async def _finish(self,item_id,target,error=None,retry=None,message_id=None,expected=(PublicationStatus.SENDING,)):
  async with self.transactions() as tx:
   row=await tx._session.get(SignalCommentPublicationRecord,item_id)
   if row and row.status in expected:
    row.status=target;row.last_error_category=error;row.retry_at=retry;row.updated_at=self.clock()
    if target is PublicationStatus.SENT:row.telegram_message_id=message_id;row.sent_at=self.clock()
   await tx.commit()
 async def recover_unexpected_failure(self,item_id):
  async with self.transactions() as tx:
   row=await tx._session.get(SignalCommentPublicationRecord,item_id)
   if row:
    if row.status is PublicationStatus.SENDING:row.status=PublicationStatus.DELIVERY_UNKNOWN;row.last_error_category="unexpected_processing_failure"
    elif row.status is PublicationStatus.RENDERING:row.status=PublicationStatus.PENDING;row.last_error_category="render_interrupted"
    row.updated_at=self.clock()
   await tx.commit()

class SignalCommentPublicationWorker:
 def __init__(self,service,concurrency=4,sweep_seconds=2):self.service=service;self.concurrency=concurrency;self.sweep_seconds=sweep_seconds;self._stop=asyncio.Event();self._wake=asyncio.Event();self.task=None
 def wake(self):self._wake.set()
 async def start(self):
  await self.service.recover();self._stop.clear();self.task=asyncio.create_task(self._run())
 async def close(self):self._stop.set();self._wake.set();await self.task if self.task else asyncio.sleep(0)
 async def _run(self):
  while not self._stop.is_set():
   self._wake.clear();ids=await self.service.pending_ids()
   for i in range(0,len(ids),self.concurrency):
    results=await asyncio.gather(*(self.service.process(x) for x in ids[i:i+self.concurrency]),return_exceptions=True)
    for item,result in zip(ids[i:i+self.concurrency],results):
     if isinstance(result,BaseException):await self.service.recover_unexpected_failure(item)
   try:await asyncio.wait_for(self._wake.wait(),self.sweep_seconds)
   except TimeoutError:pass
