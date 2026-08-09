"""DB-backed Signalist open-signal browsing and atomic cancellation."""
from dataclasses import dataclass
from datetime import datetime,timezone
from decimal import Decimal
from sqlalchemy import func,or_,select,update
from chainura.application.pagination import Page,PageRequest
from chainura.domain.monitoring import CandidateStatus,MonitorStage,NotificationStatus,PromptStatus
from chainura.domain.publication import PublicationRequestStatus,PublicationStatus
from chainura.domain.signal import SignalLifecycleStatus,TradeDirection
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.models import (BreakevenPromptRecord,DestinationPublicationRecord,
 DestinationUpdatePublicationRecord,InitialPublicationNotificationRecord,InitialPublicationRequestRecord,
 SignalCancellationPublicationRecord,SignalDetailsPublicationRecord,SignalMonitorStateRecord,SignalRecord,
 SignalTakeProfitRecord,SignalUpdateCandidateRecord,SignalistNotificationRecord,SignalistRecord,
 UpdatePublicationRequestRecord)

OPEN_STAGES=(MonitorStage.PRE_TP1,MonitorStage.POST_TP1)

@dataclass(frozen=True,slots=True)
class ActiveSignalView:
 id:int;symbol:str;direction:TradeDirection;entry:Decimal;leverage:int;status:SignalLifecycleStatus
 take_profits:tuple[Decimal,...];stop_loss:Decimal|None;monitor_stage:MonitorStage|None;highest_tp_index:int;created_at:datetime

@dataclass(frozen=True,slots=True)
class CancellationResult:
 cancelled:bool;already_cancelled:bool;publication_count:int

class ActiveSignalService:
 def __init__(self,transactions,clock=lambda:datetime.now(timezone.utc),cancellation_wake=None):self.transactions=transactions;self.clock=clock;self.cancellation_wake=cancellation_wake
 async def list_open(self,signalist_id:int,page:PageRequest)->Page[ActiveSignalView]:
  async with self.transactions() as tx:
   s=tx._session;eligible=or_(SignalRecord.status==SignalLifecycleStatus.PENDING_DETAILS,
    (SignalRecord.status==SignalLifecycleStatus.ACTIVE)&SignalMonitorStateRecord.stage.in_(OPEN_STAGES))
   base=select(SignalRecord,SignalMonitorStateRecord).outerjoin(SignalMonitorStateRecord,SignalMonitorStateRecord.signal_id==SignalRecord.id).where(SignalRecord.signalist_id==signalist_id,eligible)
   total=await s.scalar(select(func.count()).select_from(base.subquery())) or 0
   rows=(await s.execute(base.order_by(SignalRecord.created_at.desc(),SignalRecord.id.desc()).offset(page.offset).limit(page.page_size))).all()
   ids=[r.id for r,_ in rows];tpmap={i:[] for i in ids}
   if ids:
    for tp in (await s.scalars(select(SignalTakeProfitRecord).where(SignalTakeProfitRecord.signal_id.in_(ids)).order_by(SignalTakeProfitRecord.signal_id,SignalTakeProfitRecord.position))).all():tpmap[tp.signal_id].append(tp.price)
   items=tuple(self._view(r,m,tuple(tpmap[r.id])) for r,m in rows)
  return Page(items,page.page,page.page_size,total)
 async def get_open(self,signal_id:int,signalist_id:int)->ActiveSignalView|None:
  async with self.transactions() as tx:
   s=tx._session;r=await s.get(SignalRecord,signal_id);m=await s.get(SignalMonitorStateRecord,signal_id)
   if not r or r.signalist_id!=signalist_id or not (r.status is SignalLifecycleStatus.PENDING_DETAILS or r.status is SignalLifecycleStatus.ACTIVE and m and m.stage in OPEN_STAGES):return None
   tps=tuple(x.price for x in (await s.scalars(select(SignalTakeProfitRecord).where(SignalTakeProfitRecord.signal_id==signal_id).order_by(SignalTakeProfitRecord.position))).all())
   return self._view(r,m,tps)
 def _view(self,r,m,tps):return ActiveSignalView(r.id,r.canonical_symbol,r.direction,r.entry_price,r.leverage,r.status,tps,r.stop_loss,m.stage if m else None,m.highest_tp_index if m else 0,r.created_at)
 async def cancel_signal(self,signal_id:int,signalist_id:int)->CancellationResult:
  now=self.clock()
  async with self.transactions() as tx:
   s=tx._session;actor=await s.get(SignalistRecord,signalist_id);row=await s.get(SignalRecord,signal_id)
   if not actor or actor.status is not SignalistStatus.ACTIVE or not row or row.signalist_id!=signalist_id:return CancellationResult(False,False,0)
   if row.status is SignalLifecycleStatus.CANCELLED:return CancellationResult(False,True,0)
   eligible=(row.status is SignalLifecycleStatus.PENDING_DETAILS)
   if row.status is SignalLifecycleStatus.ACTIVE:
    state=await s.get(SignalMonitorStateRecord,signal_id);eligible=bool(state and state.stage in OPEN_STAGES)
   if not eligible:return CancellationResult(False,False,0)
   # CAS includes lifecycle; the transaction's prior monitor-state read keeps the
   # terminal-stage eligibility decision in the same SQLite write transaction.
   eligibility=(SignalRecord.status==SignalLifecycleStatus.PENDING_DETAILS) if row.status is SignalLifecycleStatus.PENDING_DETAILS else SignalRecord.id.in_(select(SignalMonitorStateRecord.signal_id).where(SignalMonitorStateRecord.signal_id==signal_id,SignalMonitorStateRecord.stage.in_(OPEN_STAGES)))
   changed=(await s.execute(update(SignalRecord).where(SignalRecord.id==signal_id,SignalRecord.signalist_id==signalist_id,SignalRecord.status==row.status,eligibility).values(status=SignalLifecycleStatus.CANCELLED,cancelled_at=now,updated_at=now))).rowcount
   if changed!=1:return CancellationResult(False,False,0)
   await s.execute(update(SignalUpdateCandidateRecord).where(SignalUpdateCandidateRecord.signal_id==signal_id,SignalUpdateCandidateRecord.status.in_((CandidateStatus.AWAITING_NOTIFICATION,CandidateStatus.AWAITING_DECISION,CandidateStatus.APPROVED))).values(status=CandidateStatus.SUPERSEDED,updated_at=now))
   await s.execute(update(BreakevenPromptRecord).where(BreakevenPromptRecord.signal_id==signal_id,BreakevenPromptRecord.status.in_((PromptStatus.PENDING_NOTIFICATION,PromptStatus.AWAITING_RESPONSE))).values(status=PromptStatus.SUPERSEDED,expired_at=now,updated_at=now))
   related=or_(SignalistNotificationRecord.candidate_id.in_(select(SignalUpdateCandidateRecord.id).where(SignalUpdateCandidateRecord.signal_id==signal_id)),SignalistNotificationRecord.prompt_id.in_(select(BreakevenPromptRecord.id).where(BreakevenPromptRecord.signal_id==signal_id)))
   await s.execute(update(SignalistNotificationRecord).where(related,SignalistNotificationRecord.status.in_((NotificationStatus.PENDING,NotificationStatus.RETRY_WAIT))).values(status=NotificationStatus.SUPERSEDED,updated_at=now))
   await s.execute(update(InitialPublicationRequestRecord).where(InitialPublicationRequestRecord.signal_id==signal_id,InitialPublicationRequestRecord.status.in_((PublicationRequestStatus.PENDING,PublicationRequestStatus.PLANNING,PublicationRequestStatus.PLANNED))).values(status=PublicationRequestStatus.SUPERSEDED,updated_at=now))
   await s.execute(update(DestinationPublicationRecord).where(DestinationPublicationRecord.signal_id==signal_id,DestinationPublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.READY,PublicationStatus.RETRY_WAIT,PublicationStatus.BLOCKED_TEMPLATE))).values(status=PublicationStatus.SUPERSEDED,updated_at=now))
   await s.execute(update(InitialPublicationNotificationRecord).where(InitialPublicationNotificationRecord.signal_id==signal_id,InitialPublicationNotificationRecord.status.in_(("pending","retry_wait"))).values(status="superseded",updated_at=now))
   await s.execute(update(SignalDetailsPublicationRecord).where(SignalDetailsPublicationRecord.signal_id==signal_id,SignalDetailsPublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.RETRY_WAIT,PublicationStatus.BLOCKED_TEMPLATE))).values(status=PublicationStatus.SUPERSEDED,updated_at=now))
   await s.execute(update(UpdatePublicationRequestRecord).where(UpdatePublicationRequestRecord.signal_id==signal_id,UpdatePublicationRequestRecord.status.in_((PublicationRequestStatus.PENDING,PublicationRequestStatus.WAITING_INITIAL,PublicationRequestStatus.PLANNING,PublicationRequestStatus.PLANNED))).values(status=PublicationRequestStatus.SUPERSEDED,superseded_at=now,updated_at=now))
   await s.execute(update(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.signal_id==signal_id,DestinationUpdatePublicationRecord.status.in_((PublicationStatus.PENDING,PublicationStatus.RENDERING,PublicationStatus.READY,PublicationStatus.RETRY_WAIT,PublicationStatus.BLOCKED_TEMPLATE))).values(status=PublicationStatus.SUPERSEDED,updated_at=now))
   destinations=(await s.scalars(select(DestinationPublicationRecord.destination_id).where(DestinationPublicationRecord.signal_id==signal_id,DestinationPublicationRecord.status==PublicationStatus.SENT,DestinationPublicationRecord.telegram_message_id>0))).all()
   for did in destinations:s.add(SignalCancellationPublicationRecord(signal_id=signal_id,destination_id=did,status=PublicationStatus.PENDING,created_at=now,updated_at=now))
   await s.flush()
   await tx.commit();count=len(set(destinations))
  if self.cancellation_wake:self.cancellation_wake()
  return CancellationResult(True,False,count)
