from __future__ import annotations
from datetime import datetime
from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from chainura.domain.destination import DestinationStatus
from chainura.domain.monitoring import CandidateStatus, SignalEventKind
from chainura.domain.publication import PublicationRequestStatus, PublicationStatus, TERMINAL_PUBLICATION_STATUSES, UpdatePublicationDelivery, select_event_exchange
from chainura.domain.signalist import SignalistStatus
from chainura.domain.signal import SignalLifecycleStatus
from chainura.errors import PublicationPersistenceError
from chainura.persistence.models import (DestinationExchangePriorityRecord,DestinationPublicationRecord,DestinationRecord,DestinationSignalistRecord,DestinationUpdatePublicationRecord,InitialPublicationRequestRecord,SignalDetailsPublicationRecord,SignalCommentPublicationRecord,SignalCancellationPublicationRecord,SignalEventRecord,SignalEventSourcePriceRecord,SignalRecord,SignalUpdateCandidateRecord,SignalistRecord,UpdatePublicationRequestRecord)
UPDATE_TERMINAL=TERMINAL_PUBLICATION_STATUSES|{PublicationStatus.BLOCKED_TEMPLATE}
class SqlAlchemyUpdatePublicationRepository:
 def __init__(self,session):self.session=session
 async def create_request_for_candidate(self,candidate_id,now):
  c=await self.session.get(SignalUpdateCandidateRecord,candidate_id)
  if c is None or c.status is not CandidateStatus.APPROVED or c.superseded_by_event_id is not None:return None
  existing=await self.session.scalar(select(UpdatePublicationRequestRecord.id).where(UpdatePublicationRequestRecord.candidate_id==candidate_id))
  if existing:return existing
  r=UpdatePublicationRequestRecord(candidate_id=c.id,signal_id=c.signal_id,event_id=c.event_id,status=PublicationRequestStatus.PENDING,created_at=now,updated_at=now);self.session.add(r)
  try:await self.session.flush()
  except IntegrityError:
   return await self.session.scalar(select(UpdatePublicationRequestRecord.id).where(UpdatePublicationRequestRecord.candidate_id==candidate_id))
  return r.id
 async def reconcile_requests(self,now):
  ids=(await self.session.scalars(select(SignalUpdateCandidateRecord.id).where(SignalUpdateCandidateRecord.status==CandidateStatus.APPROVED,SignalUpdateCandidateRecord.superseded_by_event_id.is_(None),~SignalUpdateCandidateRecord.id.in_(select(UpdatePublicationRequestRecord.candidate_id))))).all()
  for cid in ids:await self.create_request_for_candidate(cid,now)
  return len(ids)
 async def recover(self,now):
  await self.reconcile_requests(now)
  affected=set((await self.session.scalars(select(DestinationUpdatePublicationRecord.request_id).where(
   DestinationUpdatePublicationRecord.status.in_((PublicationStatus.RENDERING,PublicationStatus.SENDING))
   | ((DestinationUpdatePublicationRecord.status==PublicationStatus.RETRY_WAIT)&(DestinationUpdatePublicationRecord.retry_at<=now))))).all())
  affected.update((await self.session.scalars(select(UpdatePublicationRequestRecord.id).join(
   DestinationUpdatePublicationRecord,DestinationUpdatePublicationRecord.request_id==UpdatePublicationRequestRecord.id).where(
   UpdatePublicationRequestRecord.status.in_((PublicationRequestStatus.PLANNING,PublicationRequestStatus.PLANNED))).distinct())).all())
  await self.session.execute(update(UpdatePublicationRequestRecord).where(UpdatePublicationRequestRecord.status==PublicationRequestStatus.PLANNING).values(status=PublicationRequestStatus.PENDING,updated_at=now))
  await self.session.execute(update(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.status==PublicationStatus.RENDERING).values(status=PublicationStatus.PENDING,updated_at=now))
  await self.session.execute(update(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.status==PublicationStatus.SENDING).values(status=PublicationStatus.DELIVERY_UNKNOWN,last_error_category='restart_after_send_started',updated_at=now))
  await self.session.execute(update(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.status==PublicationStatus.RETRY_WAIT,DestinationUpdatePublicationRecord.retry_at<=now).values(status=PublicationStatus.PENDING,retry_at=None,updated_at=now))
  for request_id in affected:await self.complete_request(request_id,now)
 async def plan_pending(self,now,limit=50):
  ids=(await self.session.scalars(select(UpdatePublicationRequestRecord.id).where(UpdatePublicationRequestRecord.status.in_((PublicationRequestStatus.PENDING,PublicationRequestStatus.WAITING_INITIAL))).order_by(UpdatePublicationRequestRecord.created_at).limit(limit))).all()
  for rid in ids:await self.plan(rid,now)
 async def plan(self,request_id,now):
  req=await self.session.get(UpdatePublicationRequestRecord,request_id)
  if req is None or req.status not in (PublicationRequestStatus.PENDING,PublicationRequestStatus.WAITING_INITIAL):return False
  initial=await self.session.scalar(select(InitialPublicationRequestRecord).where(InitialPublicationRequestRecord.signal_id==req.signal_id))
  if initial is not None and initial.status not in (PublicationRequestStatus.COMPLETED,PublicationRequestStatus.FAILED):
   req.status=PublicationRequestStatus.WAITING_INITIAL;req.updated_at=now;return True
  req.status=PublicationRequestStatus.PLANNING;req.updated_at=now
  signal=await self.session.get(SignalRecord,req.signal_id);event=await self.session.get(SignalEventRecord,req.event_id);candidate=await self.session.get(SignalUpdateCandidateRecord,req.candidate_id)
  if not signal or signal.status is not SignalLifecycleStatus.ACTIVE or not event or not candidate or candidate.superseded_by_event_id is not None:
   req.status=PublicationRequestStatus.SUPERSEDED;req.superseded_at=now;req.updated_at=now;return True
  sources=frozenset((await self.session.scalars(select(SignalEventSourcePriceRecord.exchange).where(SignalEventSourcePriceRecord.event_id==req.event_id))).all())
  initials=(await self.session.execute(select(DestinationPublicationRecord.destination_id).where(DestinationPublicationRecord.signal_id==req.signal_id,DestinationPublicationRecord.status==PublicationStatus.SENT,DestinationPublicationRecord.telegram_message_id>0))).all()
  for (destination_id,) in initials:
   priorities=(await self.session.scalars(select(DestinationExchangePriorityRecord.exchange).where(DestinationExchangePriorityRecord.destination_id==destination_id).order_by(DestinationExchangePriorityRecord.position))).all()
   selected=select_event_exchange(signal.mode,signal.selected_exchange,priorities,sources)
   status=PublicationStatus.PENDING if selected else PublicationStatus.SKIPPED_NO_EVENT_SOURCE
   if selected is None:selected=next(iter(sources),None)
   if selected is None:continue
   self.session.add(DestinationUpdatePublicationRecord(request_id=req.id,candidate_id=req.candidate_id,signal_id=req.signal_id,event_id=req.event_id,destination_id=destination_id,selected_exchange=selected,event_kind=event.kind,status=status,created_at=now,updated_at=now))
  await self.session.flush();req.status=PublicationRequestStatus.PLANNED;req.planned_at=now;req.updated_at=now;await self.complete_request(req.id,now);return True
 async def supersede_obsolete_tp(self,now):
  rows=(await self.session.execute(select(DestinationUpdatePublicationRecord,SignalEventRecord).join(SignalEventRecord,SignalEventRecord.id==DestinationUpdatePublicationRecord.event_id).where(SignalEventRecord.kind==SignalEventKind.TP_HIT,DestinationUpdatePublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.RETRY_WAIT,PublicationStatus.BLOCKED_TEMPLATE))))).all()
  affected=set()
  for d,e in rows:
   newer=await self.session.scalar(select(func.count()).select_from(SignalEventRecord).where(SignalEventRecord.signal_id==e.signal_id,SignalEventRecord.kind==SignalEventKind.TP_HIT,SignalEventRecord.tp_index>e.tp_index))
   if newer:
    d.status=PublicationStatus.SUPERSEDED;d.updated_at=now;affected.add(d.request_id)
  for request_id in affected:await self.complete_request(request_id,now)
  return len(affected)
 async def _is_obsolete_tp(self,row):
  event=await self.session.get(SignalEventRecord,row.event_id)
  candidate=await self.session.get(SignalUpdateCandidateRecord,row.candidate_id)
  request=await self.session.get(UpdatePublicationRequestRecord,row.request_id)
  if request is None or request.status is PublicationRequestStatus.SUPERSEDED:return True
  if candidate is None or candidate.superseded_by_event_id is not None:return True
  if event is None or event.kind is not SignalEventKind.TP_HIT:return False
  newer=await self.session.scalar(select(func.count()).select_from(SignalEventRecord).where(SignalEventRecord.signal_id==event.signal_id,SignalEventRecord.kind==SignalEventKind.TP_HIT,SignalEventRecord.tp_index>event.tp_index))
  return bool(newer)
 async def pending_delivery_ids(self,now,limit=100):
  return tuple((await self.session.scalars(select(DestinationUpdatePublicationRecord.id).where((DestinationUpdatePublicationRecord.status==PublicationStatus.PENDING)|((DestinationUpdatePublicationRecord.status==PublicationStatus.RETRY_WAIT)&(DestinationUpdatePublicationRecord.retry_at<=now))).order_by(DestinationUpdatePublicationRecord.signal_id,DestinationUpdatePublicationRecord.destination_id,DestinationUpdatePublicationRecord.event_id).limit(limit))).all())
 async def claim(self,id,now):
  row=await self.session.get(DestinationUpdatePublicationRecord,id)
  if row is None or row.status not in (PublicationStatus.PENDING,PublicationStatus.RETRY_WAIT):return None
  earlier=await self.session.scalar(select(func.count()).select_from(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.signal_id==row.signal_id,DestinationUpdatePublicationRecord.destination_id==row.destination_id,DestinationUpdatePublicationRecord.id!=row.id,DestinationUpdatePublicationRecord.event_id<row.event_id,DestinationUpdatePublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.SENDING,PublicationStatus.RETRY_WAIT))))
  if earlier:return None
  # A Phase 7 Details row is a durable chain barrier while its Telegram result
  # is still unknown. Legacy signals have no row and remain unblocked.
  details_in_flight=await self.session.scalar(select(exists().where(
   SignalDetailsPublicationRecord.signal_id==row.signal_id,
   SignalDetailsPublicationRecord.destination_id==row.destination_id,
   SignalDetailsPublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.SENDING,PublicationStatus.RETRY_WAIT)))))
  if details_in_flight:return None
  # Only an older Comment is a barrier. A newer Comment waits for this update,
  # avoiding a symmetric PENDING/PENDING deadlock while preserving chain order.
  comment_in_flight=await self.session.scalar(select(exists().where(SignalCommentPublicationRecord.signal_id==row.signal_id,SignalCommentPublicationRecord.destination_id==row.destination_id,SignalCommentPublicationRecord.created_at<=row.created_at,SignalCommentPublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.SENDING,PublicationStatus.RETRY_WAIT,PublicationStatus.DELIVERY_UNKNOWN)))))
  if comment_in_flight:return None
  row.status=PublicationStatus.RENDERING;row.retry_at=None;row.first_attempt_at=row.first_attempt_at or now;row.updated_at=now
  dest=await self.session.get(DestinationRecord,row.destination_id)
  return UpdatePublicationDelivery(row.id,row.request_id,row.candidate_id,row.signal_id,row.event_id,row.destination_id,row.selected_exchange,row.event_kind,row.attempt_count,dest.telegram_chat_id if dest else 0) if dest else None
 async def prepare_send(self,id,now):
  row=await self.session.get(DestinationUpdatePublicationRecord,id);signal=await self.session.get(SignalRecord,row.signal_id) if row else None;dest=await self.session.get(DestinationRecord,row.destination_id) if row else None;actor=await self.session.get(SignalistRecord,signal.signalist_id) if signal else None
  if not row or row.status is not PublicationStatus.RENDERING:return 'lost',None
  if signal is None or signal.status is not SignalLifecycleStatus.ACTIVE:
   row.status=PublicationStatus.SUPERSEDED;row.updated_at=now;return row.status.value,None
  if await self._is_obsolete_tp(row):
   row.status=PublicationStatus.SUPERSEDED;row.updated_at=now;return row.status.value,None
  if actor is None or actor.status is not SignalistStatus.ACTIVE:row.status=PublicationStatus.SKIPPED_SIGNALIST_INACTIVE
  elif dest is None or dest.status is not DestinationStatus.ACTIVE:row.status=PublicationStatus.SKIPPED_DESTINATION_INACTIVE
  elif not await self.session.scalar(select(exists().where(DestinationSignalistRecord.destination_id==row.destination_id,DestinationSignalistRecord.signalist_id==signal.signalist_id))):row.status=PublicationStatus.SKIPPED_UNASSIGNED
  elif not await self.session.scalar(select(exists().where(DestinationPublicationRecord.signal_id==row.signal_id,DestinationPublicationRecord.destination_id==row.destination_id,DestinationPublicationRecord.status==PublicationStatus.SENT,DestinationPublicationRecord.telegram_message_id>0))):row.status=PublicationStatus.SKIPPED_NO_INITIAL_MESSAGE
  else:
   parent=await self.last_published_message_id(row.signal_id,row.destination_id)
   if not parent:row.status=PublicationStatus.SKIPPED_NO_INITIAL_MESSAGE
   else:
    row.status=PublicationStatus.SENDING;row.reply_to_message_id=parent;row.attempt_count+=1;row.updated_at=now;return 'sending',parent
  row.updated_at=now;return row.status.value,None
 async def transition(self,id,expected,target,now,error=None,message_id=None,retry_at=None,template_sha=None):
  row=await self.session.get(DestinationUpdatePublicationRecord,id)
  if row is None or row.status is not expected:return False
  row.status=target;row.updated_at=now;row.last_error_category=error;row.retry_at=retry_at
  if template_sha:row.template_sha256=template_sha
  if target is PublicationStatus.SENT:row.telegram_message_id=message_id;row.sent_at=now
  return True
 async def last_published_message_id(self,signal_id,destination_id):
  candidates=[]
  for model in (DestinationUpdatePublicationRecord,SignalCommentPublicationRecord,SignalCancellationPublicationRecord,SignalDetailsPublicationRecord,DestinationPublicationRecord):
   row=(await self.session.execute(select(model.telegram_message_id,model.sent_at,model.id).where(model.signal_id==signal_id,model.destination_id==destination_id,model.status==PublicationStatus.SENT,model.telegram_message_id>0).order_by(model.sent_at.desc(),model.id.desc()).limit(1))).first()
   if row:candidates.append(row)
  return max(candidates,key=lambda row:(row.sent_at,row.id)).telegram_message_id if candidates else None
 async def complete_request(self,request_id,now):
  unfinished=await self.session.scalar(select(func.count()).select_from(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.request_id==request_id,DestinationUpdatePublicationRecord.status.not_in(tuple(TERMINAL_PUBLICATION_STATUSES|{PublicationStatus.SUPERSEDED}))))
  if unfinished:return
  total=await self.session.scalar(select(func.count()).select_from(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.request_id==request_id))
  superseded=await self.session.scalar(select(func.count()).select_from(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.request_id==request_id,DestinationUpdatePublicationRecord.status==PublicationStatus.SUPERSEDED))
  target=PublicationRequestStatus.SUPERSEDED if total and total==superseded else PublicationRequestStatus.COMPLETED
  values={"status":target,"completed_at":now,"updated_at":now}
  if target is PublicationRequestStatus.SUPERSEDED:values["superseded_at"]=now
  await self.session.execute(update(UpdatePublicationRequestRecord).where(UpdatePublicationRequestRecord.id==request_id,UpdatePublicationRequestRecord.status.in_((PublicationRequestStatus.PLANNED,PublicationRequestStatus.PLANNING,PublicationRequestStatus.PENDING,PublicationRequestStatus.WAITING_INITIAL))).values(**values))
 async def unblock_templates(self,now):
  await self.session.execute(update(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.status==PublicationStatus.BLOCKED_TEMPLATE).values(status=PublicationStatus.PENDING,last_error_category=None,updated_at=now))
