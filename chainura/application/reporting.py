from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, and_, desc, exists
from sqlalchemy.ext.asyncio import async_sessionmaker
from chainura.domain.monitoring import MonitorStage, ResultKind, SignalEventKind
from chainura.domain.publication import PublicationStatus
from chainura.domain.signal import SignalLifecycleStatus, SignalMode, PriceSourceType
from chainura.persistence.models import SignalRecord, SignalistRecord, SignalMonitorStateRecord, SignalEventRecord, DestinationPublicationRecord
TEHRAN=ZoneInfo("Asia/Tehran"); PAGE_SIZE=10
@dataclass(frozen=True,slots=True)
class ReportFilter:
    start_utc:datetime; end_utc:datetime; signalist_id:int|None=None
@dataclass(frozen=True,slots=True)
class ReportSummary:
    total_signals:int=0; pending_signals:int=0; open_signals:int=0; cancelled_signals:int=0; resulted_signals:int=0; wins:int=0; losses:int=0; breakevens:int=0; win_rate:Decimal|None=None; total_roi:Decimal=Decimal("0"); average_roi:Decimal|None=None; average_positive_roi:Decimal|None=None; maximum_positive_roi:Decimal|None=None; average_negative_roi:Decimal|None=None; minimum_negative_roi:Decimal|None=None; tp1_count:int=0; tp2_count:int=0; tp3_plus_count:int=0; composite_count:int=0; single_exchange_count:int=0; confirmed_published_count:int=0; unpublished_count:int=0

    @property
    def active_signals(self)->int:
        """Phase 6 compatibility alias: active now means an open ACTIVE trade."""
        return self.open_signals
@dataclass(frozen=True,slots=True)
class TradeHistoryItem:
    signal_id:int; signalist_display_name:str; canonical_symbol:str; direction:str; created_at_tehran:datetime; source_label:str; state:str; highest_tp:int|None; theoretical_roi:Decimal|None; confirmed_published:bool
@dataclass(frozen=True,slots=True)
class TradeHistoryPage:
    items:tuple[TradeHistoryItem,...]; page:int; pages:int; total:int

def _aware_utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
def tehran_day_range(day:date)->tuple[datetime,datetime]:
    start=datetime.combine(day,time.min,TEHRAN); end=start+timedelta(days=1); return start.astimezone(timezone.utc),end.astimezone(timezone.utc)
def period_filter(period:str, now:datetime, signalist_id:int|None=None, start:date|None=None, end:date|None=None)->ReportFilter:
    today=now.astimezone(TEHRAN).date()
    if period=="today": s,e=tehran_day_range(today)
    elif period=="7d": s,e=tehran_day_range(today-timedelta(days=6))[0], tehran_day_range(today)[1]
    elif period=="30d": s,e=tehran_day_range(today-timedelta(days=29))[0], tehran_day_range(today)[1]
    elif period=="custom" and start and end and start<=end: s,e=tehran_day_range(start)[0], tehran_day_range(end)[1]
    else: raise ValueError("invalid report date range")
    return ReportFilter(s,e,signalist_id)
class ReportingRepository:
    def __init__(self,sessions:async_sessionmaker): self.sessions=sessions
    def _base(self,flt:ReportFilter):
        clause=[SignalRecord.created_at>=flt.start_utc,SignalRecord.created_at<flt.end_utc]
        if flt.signalist_id is not None: clause.append(SignalRecord.signalist_id==flt.signalist_id)
        return clause
    async def summary(self,flt:ReportFilter)->ReportSummary:
        async with self.sessions() as s:
            rows=(await s.execute(select(SignalRecord,SignalMonitorStateRecord).join(SignalMonitorStateRecord,SignalMonitorStateRecord.signal_id==SignalRecord.id,isouter=True).where(*self._base(flt)))).all()
            ids=[r.SignalRecord.id for r in rows]
            published=set()
            if ids:
                published=set((await s.scalars(select(DestinationPublicationRecord.signal_id).where(DestinationPublicationRecord.signal_id.in_(ids),DestinationPublicationRecord.status==PublicationStatus.SENT,DestinationPublicationRecord.telegram_message_id>0).distinct())).all())
            total=len(rows);pending=open_count=cancelled=wins=losses=be=comp=single=tp1=tp2=tp3=0;rois=[];pos=[];neg=[]
            for sig,st in rows:
                if sig.mode is SignalMode.COMPOSITE: comp+=1
                else: single+=1
                if sig.status is SignalLifecycleStatus.PENDING_DETAILS:
                    pending+=1;continue
                if sig.status is SignalLifecycleStatus.CANCELLED:
                    cancelled+=1;continue
                if sig.status is not SignalLifecycleStatus.ACTIVE:
                    continue
                if st and st.stage in (MonitorStage.PRE_TP1,MonitorStage.POST_TP1,
                                       MonitorStage.AWAITING_FINAL_TP_ACTION,
                                       MonitorStage.DISABLED_LEGACY): open_count+=1
                if st and st.current_result_kind is ResultKind.WIN:
                    wins+=1; roi=Decimal(st.current_leveraged_roi); rois.append(roi); pos.append(roi)
                    if st.highest_tp_index==1: tp1+=1
                    elif st.highest_tp_index==2: tp2+=1
                    elif st.highest_tp_index>=3: tp3+=1
                elif st and st.current_result_kind is ResultKind.LOSS:
                    losses+=1; roi=Decimal(st.current_leveraged_roi); rois.append(roi); neg.append(roi)
                elif st and st.current_result_kind is ResultKind.NEUTRAL:
                    be+=1; rois.append(Decimal("0"))
            wr=(Decimal(wins)/Decimal(wins+losses)*Decimal("100")) if wins+losses else None
            avg=lambda xs: (sum(xs,Decimal("0"))/Decimal(len(xs))) if xs else None
            return ReportSummary(total_signals=total,pending_signals=pending,open_signals=open_count,
                cancelled_signals=cancelled,resulted_signals=wins+losses+be,wins=wins,losses=losses,
                breakevens=be,win_rate=wr,total_roi=sum(rois,Decimal("0")),average_roi=avg(rois),
                average_positive_roi=avg(pos),maximum_positive_roi=max(pos) if pos else None,
                average_negative_roi=avg(neg),minimum_negative_roi=min(neg) if neg else None,
                tp1_count=tp1,tp2_count=tp2,tp3_plus_count=tp3,composite_count=comp,
                single_exchange_count=single,confirmed_published_count=len(published),
                unpublished_count=total-len(published))
    async def history(self,flt:ReportFilter,page:int=1)->TradeHistoryPage:
        page=max(1,int(page)); offset=(page-1)*PAGE_SIZE
        async with self.sessions() as s:
            total=await s.scalar(select(func.count()).select_from(SignalRecord).where(*self._base(flt))) or 0
            rows=(await s.execute(select(SignalRecord,SignalistRecord,SignalMonitorStateRecord).join(SignalistRecord,SignalistRecord.id==SignalRecord.signalist_id).join(SignalMonitorStateRecord,SignalMonitorStateRecord.signal_id==SignalRecord.id,isouter=True).where(*self._base(flt)).order_by(desc(SignalRecord.created_at),desc(SignalRecord.id)).limit(PAGE_SIZE).offset(offset))).all()
            ids=[r.SignalRecord.id for r in rows]; published=set()
            if ids: published=set((await s.scalars(select(DestinationPublicationRecord.signal_id).where(DestinationPublicationRecord.signal_id.in_(ids),DestinationPublicationRecord.status==PublicationStatus.SENT,DestinationPublicationRecord.telegram_message_id>0).distinct())).all())
            items=[]
            for sig,sl,st in rows:
                highest=st.highest_tp_index if st and st.highest_tp_index else None
                roi=Decimal(st.current_leveraged_roi) if st and st.current_leveraged_roi is not None else None
                if sig.status is SignalLifecycleStatus.PENDING_DETAILS:
                    state="Pending Details";roi=None
                elif sig.status is SignalLifecycleStatus.CANCELLED:
                    state="Cancelled"+(f" after TP{highest}" if highest else "");roi=None
                elif not st or st.current_result_kind is None: state="Active without result"
                elif st.current_result_kind is ResultKind.WIN:
                    state=("Awaiting Final TP Action" if st.stage is MonitorStage.AWAITING_FINAL_TP_ACTION
                           else "Active Win" if st.stage is MonitorStage.POST_TP1
                           else "Completed Win")+f" at TP{highest}"
                elif st.current_result_kind is ResultKind.LOSS: state="Loss"
                else: state="Breakeven"
                items.append(TradeHistoryItem(sig.id,sl.display_name,sig.canonical_symbol,sig.direction.value,_aware_utc(sig.created_at).astimezone(TEHRAN),"Composite" if sig.mode is SignalMode.COMPOSITE else "Single Exchange",state,highest,roi,sig.id in published))
            pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE)
            return TradeHistoryPage(tuple(items),page,pages,total)
class ReportingService:
    def __init__(self,repo:ReportingRepository): self.repo=repo
    async def summary(self,flt): return await self.repo.summary(flt)
    async def history(self,flt,page=1): return await self.repo.history(flt,page)
