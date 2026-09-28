"""Read-only, privacy-aware performance reporting.

All three reporting surfaces use the projection in this module.  In particular,
Telegram handlers never interpret monitor events or calculate ROI themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select, union_all
from sqlalchemy.ext.asyncio import async_sessionmaker

from chainura.domain.monitoring import MonitorStage, ResultKind
from chainura.domain.publication import PublicationStatus
from chainura.domain.signal import SignalEntryType, SignalLifecycleStatus, SignalMode
from chainura.persistence.models import (DestinationPublicationRecord, DestinationRecord,
    LimitPendingPublicationRecord, SignalMonitorStateRecord, SignalPositionStateRecord,
    SignalRecord, SignalistRecord)

TEHRAN = ZoneInfo("Asia/Tehran")
PAGE_SIZE = 10


@dataclass(frozen=True, slots=True)
class ReportFilter:
    start_utc: datetime
    end_utc: datetime
    signalist_id: int | None = None


@dataclass(frozen=True, slots=True)
class DestinationReportFilter:
    destination_id: int
    start_utc: datetime
    end_utc: datetime

    def __post_init__(self):
        if self.destination_id < 1 or self.start_utc >= self.end_utc:
            raise ValueError("invalid destination report filter")


@dataclass(frozen=True, slots=True)
class PerformanceProjection:
    signal_id: int
    owner_signalist_id: int
    canonical_symbol: str
    direction: str
    entry_type: str
    source_label: str
    lifecycle_status: SignalLifecycleStatus
    monitor_stage: MonitorStage | None
    highest_tp: int | None
    result_kind: ResultKind | None
    report_roi: Decimal | None
    created_at: datetime
    pending: bool
    open: bool
    cancelled: bool
    state: str


@dataclass(frozen=True, slots=True)
class ReportSummary:
    total_signals: int = 0
    pending_signals: int = 0
    open_signals: int = 0
    cancelled_signals: int = 0
    resulted_signals: int = 0
    wins: int = 0
    losses: int = 0
    breakevens: int = 0  # compatibility name; presented as Neutral in Phase 7
    win_rate: Decimal | None = None
    total_roi: Decimal = Decimal("0")
    average_roi: Decimal | None = None
    average_positive_roi: Decimal | None = None
    maximum_positive_roi: Decimal | None = None
    average_negative_roi: Decimal | None = None
    minimum_negative_roi: Decimal | None = None
    tp1_count: int = 0
    tp2_count: int = 0
    tp3_plus_count: int = 0
    composite_count: int = 0
    single_exchange_count: int = 0
    confirmed_published_count: int = 0
    unpublished_count: int = 0

    @property
    def neutral(self): return self.breakevens
    @property
    def active_signals(self): return self.open_signals
    @property
    def best_roi(self): return self.maximum_positive_roi if self.maximum_positive_roi is not None else (self.minimum_negative_roi if self.minimum_negative_roi is not None else None)
    @property
    def worst_roi(self): return self.minimum_negative_roi if self.minimum_negative_roi is not None else self.maximum_positive_roi


@dataclass(frozen=True, slots=True)
class TradeHistoryItem:
    signal_id: int; signalist_display_name: str; canonical_symbol: str; direction: str
    created_at_tehran: datetime; source_label: str; state: str; highest_tp: int | None
    theoretical_roi: Decimal | None; confirmed_published: bool


@dataclass(frozen=True, slots=True)
class SignalistTradeHistoryItem:
    trade_id: int; canonical_symbol: str; direction: str; entry_type: str; source_label: str
    created_at_tehran: datetime; state: str; highest_tp: int | None; realized_weighted_roi: Decimal | None


@dataclass(frozen=True, slots=True)
class DestinationTradeHistoryItem:
    trade_id: int; canonical_symbol: str; direction: str; entry_type: str; source_label: str
    selected_exchange: str; published_at_tehran: datetime; state: str
    highest_tp: int | None; realized_weighted_roi: Decimal | None


@dataclass(frozen=True, slots=True)
class TradeHistoryPage:
    items: tuple; page: int; pages: int; total: int


def _aware_utc(dt): return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def tehran_day_range(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, TEHRAN)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def period_filter(period: str, now: datetime, signalist_id: int | None = None,
                  start: date | None = None, end: date | None = None) -> ReportFilter:
    today = now.astimezone(TEHRAN).date()
    if period == "today": s, e = tehran_day_range(today)
    elif period == "7d": s, e = tehran_day_range(today-timedelta(days=6))[0], tehran_day_range(today)[1]
    elif period == "30d": s, e = tehran_day_range(today-timedelta(days=29))[0], tehran_day_range(today)[1]
    elif period == "custom" and start and end and start <= end: s, e = tehran_day_range(start)[0], tehran_day_range(end)[1]
    else: raise ValueError("invalid report date range")
    return ReportFilter(s, e, signalist_id)


def destination_period_filter(destination_id: int, period: str, now: datetime,
                              start: date | None = None, end: date | None = None):
    value = period_filter(period, now, start=start, end=end)
    return DestinationReportFilter(destination_id, value.start_utc, value.end_utc)


def _projection(sig, monitor, position) -> PerformanceProjection:
    cancelled = sig.status is SignalLifecycleStatus.CANCELLED
    pending = sig.status is SignalLifecycleStatus.PENDING_DETAILS
    open_ = sig.status in (SignalLifecycleStatus.PENDING_ENTRY, SignalLifecycleStatus.ENTRY_TRIGGERED)
    if sig.status is SignalLifecycleStatus.ACTIVE and monitor and monitor.stage in {
        MonitorStage.PRE_TP1, MonitorStage.POST_TP1, MonitorStage.AWAITING_FINAL_TP_ACTION,
        MonitorStage.DISABLED_LEGACY}:
        open_ = True
    result = None; roi = None
    if not cancelled and position is not None:
        weighted = Decimal(position.realized_weighted_roi)
        if position.is_closed:
            roi = weighted
            result = ResultKind.WIN if weighted > 0 else ResultKind.LOSS if weighted < 0 else ResultKind.NEUTRAL
        elif monitor and monitor.current_result_kind is not None:
            result = monitor.current_result_kind
            # Zero before any realized close is not performance.
            if weighted != 0:
                roi = weighted
    elif not cancelled and monitor and monitor.current_result_kind is not None:
        result = monitor.current_result_kind
        roi = Decimal(monitor.current_leveraged_roi) if monitor.current_leveraged_roi is not None else None
    highest = monitor.highest_tp_index if monitor and monitor.highest_tp_index else None
    if pending: state = "Pending Details"
    elif sig.status is SignalLifecycleStatus.PENDING_ENTRY: state = "Limit Pending Entry"
    elif sig.status is SignalLifecycleStatus.ENTRY_TRIGGERED: state = "Limit Entry Triggered"
    elif cancelled: state = "Cancelled" + (f" after TP{highest}" if highest else "")
    elif result is ResultKind.WIN:
        prefix = "Awaiting Final TP Action" if monitor and monitor.stage is MonitorStage.AWAITING_FINAL_TP_ACTION else "Active Win" if open_ else "Completed Win"
        state = prefix + (f" at TP{highest}" if highest else "")
    elif result is ResultKind.LOSS: state = "Loss"
    elif result is ResultKind.NEUTRAL: state = "Neutral"
    else: state = "Active without result"
    return PerformanceProjection(sig.id, sig.signalist_id, sig.canonical_symbol,
        sig.direction.value, sig.entry_type.value, "Composite" if sig.mode is SignalMode.COMPOSITE else "Single Exchange",
        sig.status, monitor.stage if monitor else None, highest, result, roi,
        _aware_utc(sig.created_at), pending, open_, cancelled, state)


def summarize(items, *, published_count=0) -> ReportSummary:
    items = tuple(items); rois = [x.report_roi for x in items if x.report_roi is not None and not x.cancelled]
    wins = sum(x.result_kind is ResultKind.WIN and not x.cancelled for x in items)
    losses = sum(x.result_kind is ResultKind.LOSS and not x.cancelled for x in items)
    neutral = sum(x.result_kind is ResultKind.NEUTRAL and not x.cancelled for x in items)
    positive = [x for x in rois if x > 0]; negative = [x for x in rois if x < 0]
    avg = lambda values: sum(values, Decimal("0"))/Decimal(len(values)) if values else None
    return ReportSummary(total_signals=len(items), pending_signals=sum(x.pending for x in items),
        open_signals=sum(x.open for x in items), cancelled_signals=sum(x.cancelled for x in items),
        resulted_signals=wins+losses+neutral, wins=wins, losses=losses, breakevens=neutral,
        win_rate=Decimal(wins)*Decimal("100")/Decimal(wins+losses) if wins+losses else None,
        total_roi=sum(rois, Decimal("0")), average_roi=avg(rois), average_positive_roi=avg(positive),
        maximum_positive_roi=max(positive) if positive else None, average_negative_roi=avg(negative),
        minimum_negative_roi=min(negative) if negative else None,
        tp1_count=sum(x.highest_tp == 1 and not x.cancelled for x in items), tp2_count=sum(x.highest_tp == 2 and not x.cancelled for x in items),
        tp3_plus_count=sum(bool(x.highest_tp and x.highest_tp >= 3 and not x.cancelled) for x in items),
        composite_count=sum(x.source_label == "Composite" for x in items),
        single_exchange_count=sum(x.source_label == "Single Exchange" for x in items),
        confirmed_published_count=published_count, unpublished_count=len(items)-published_count)


class ReportingRepository:
    def __init__(self, sessions: async_sessionmaker): self.sessions = sessions
    def _base(self, flt):
        out = [SignalRecord.created_at >= flt.start_utc, SignalRecord.created_at < flt.end_utc]
        if flt.signalist_id is not None: out.append(SignalRecord.signalist_id == flt.signalist_id)
        return out
    async def _projections(self, session, clauses):
        rows = (await session.execute(select(SignalRecord, SignalMonitorStateRecord, SignalPositionStateRecord)
            .outerjoin(SignalMonitorStateRecord, SignalMonitorStateRecord.signal_id == SignalRecord.id)
            .outerjoin(SignalPositionStateRecord, SignalPositionStateRecord.signal_id == SignalRecord.id)
            .where(*clauses))).all()
        return [_projection(*row) for row in rows]
    async def summary(self, flt):
        async with self.sessions() as s:
            projections = await self._projections(s, self._base(flt)); ids = [x.signal_id for x in projections]
            published = set()
            if ids:
                published = set((await s.scalars(select(DestinationPublicationRecord.signal_id).where(
                    DestinationPublicationRecord.signal_id.in_(ids), DestinationPublicationRecord.status == PublicationStatus.SENT,
                    DestinationPublicationRecord.telegram_message_id > 0).distinct())).all())
            return summarize(projections, published_count=len(published))
    async def history(self, flt, page=1):
        page=max(1,int(page)); offset=(page-1)*PAGE_SIZE
        async with self.sessions() as s:
            total=await s.scalar(select(func.count()).select_from(SignalRecord).where(*self._base(flt))) or 0
            rows=(await s.execute(select(SignalRecord,SignalistRecord,SignalMonitorStateRecord,SignalPositionStateRecord)
                .join(SignalistRecord,SignalistRecord.id==SignalRecord.signalist_id)
                .outerjoin(SignalMonitorStateRecord,SignalMonitorStateRecord.signal_id==SignalRecord.id)
                .outerjoin(SignalPositionStateRecord,SignalPositionStateRecord.signal_id==SignalRecord.id)
                .where(*self._base(flt)).order_by(desc(SignalRecord.created_at),desc(SignalRecord.id)).limit(PAGE_SIZE).offset(offset))).all()
            ids=[r.SignalRecord.id for r in rows]; published=set()
            if ids: published=set((await s.scalars(select(DestinationPublicationRecord.signal_id).where(
                DestinationPublicationRecord.signal_id.in_(ids),DestinationPublicationRecord.status==PublicationStatus.SENT,
                DestinationPublicationRecord.telegram_message_id>0).distinct())).all())
            items=[]
            for sig,owner,monitor,position in rows:
                p=_projection(sig,monitor,position)
                items.append(TradeHistoryItem(sig.id,owner.display_name,p.canonical_symbol,p.direction,
                    p.created_at.astimezone(TEHRAN),p.source_label,p.state,p.highest_tp,p.report_roi,sig.id in published))
            return TradeHistoryPage(tuple(items),page,max(1,(total+PAGE_SIZE-1)//PAGE_SIZE),total)
    async def signalist_summary(self, owner_id, flt):
        safe=ReportFilter(flt.start_utc,flt.end_utc,int(owner_id))
        async with self.sessions() as s: return summarize(await self._projections(s,self._base(safe)))
    async def signalist_history(self, owner_id, flt, page=1):
        safe=ReportFilter(flt.start_utc,flt.end_utc,int(owner_id)); page=max(1,int(page))
        async with self.sessions() as s:
            total=await s.scalar(select(func.count()).select_from(SignalRecord).where(*self._base(safe))) or 0
            rows=(await s.execute(select(SignalRecord,SignalMonitorStateRecord,SignalPositionStateRecord)
                .outerjoin(SignalMonitorStateRecord,SignalMonitorStateRecord.signal_id==SignalRecord.id)
                .outerjoin(SignalPositionStateRecord,SignalPositionStateRecord.signal_id==SignalRecord.id)
                .where(*self._base(safe)).order_by(desc(SignalRecord.created_at),desc(SignalRecord.id))
                .limit(PAGE_SIZE).offset((page-1)*PAGE_SIZE))).all()
            result=[]
            for row in rows:
                p=_projection(*row); result.append(SignalistTradeHistoryItem(p.signal_id,p.canonical_symbol,p.direction,p.entry_type,
                    p.source_label,p.created_at.astimezone(TEHRAN),p.state,p.highest_tp,p.report_roi))
            return TradeHistoryPage(tuple(result),page,max(1,(total+PAGE_SIZE-1)//PAGE_SIZE),total)
    def _roots(self, flt):
        market=select(DestinationPublicationRecord.signal_id.label("signal_id"),DestinationPublicationRecord.sent_at.label("sent_at"),DestinationPublicationRecord.selected_exchange.label("exchange")).where(
            DestinationPublicationRecord.destination_id==flt.destination_id,DestinationPublicationRecord.origin=="market",
            DestinationPublicationRecord.status==PublicationStatus.SENT,DestinationPublicationRecord.telegram_message_id>0,
            DestinationPublicationRecord.sent_at.is_not(None),DestinationPublicationRecord.sent_at>=flt.start_utc,DestinationPublicationRecord.sent_at<flt.end_utc)
        limit=select(LimitPendingPublicationRecord.signal_id.label("signal_id"),LimitPendingPublicationRecord.sent_at.label("sent_at"),LimitPendingPublicationRecord.selected_exchange.label("exchange")).where(
            LimitPendingPublicationRecord.destination_id==flt.destination_id,LimitPendingPublicationRecord.status==PublicationStatus.SENT,
            LimitPendingPublicationRecord.telegram_message_id>0,LimitPendingPublicationRecord.sent_at.is_not(None),
            LimitPendingPublicationRecord.sent_at>=flt.start_utc,LimitPendingPublicationRecord.sent_at<flt.end_utc)
        return union_all(market,limit).subquery()
    async def destination_exists(self,destination_id):
        if not isinstance(destination_id,int) or destination_id<1:return False
        async with self.sessions() as s:return (await s.scalar(select(DestinationRecord.id).where(DestinationRecord.id==destination_id))) is not None
    async def destination_summary(self,flt):
        async with self.sessions() as s:
            if not await s.scalar(select(DestinationRecord.id).where(DestinationRecord.id==flt.destination_id)): raise ValueError("destination not found")
            roots=self._roots(flt)
            rows=(await s.execute(select(SignalRecord,SignalMonitorStateRecord,SignalPositionStateRecord)
                .join(roots,roots.c.signal_id==SignalRecord.id).outerjoin(SignalMonitorStateRecord,SignalMonitorStateRecord.signal_id==SignalRecord.id)
                .outerjoin(SignalPositionStateRecord,SignalPositionStateRecord.signal_id==SignalRecord.id))).all()
            return summarize(_projection(*row) for row in rows)
    async def destination_history(self,flt,page=1):
        page=max(1,int(page))
        async with self.sessions() as s:
            destination=await s.scalar(select(DestinationRecord).where(DestinationRecord.id==flt.destination_id))
            if destination is None: raise ValueError("destination not found")
            roots=self._roots(flt); total=await s.scalar(select(func.count()).select_from(roots)) or 0
            rows=(await s.execute(select(SignalRecord,SignalMonitorStateRecord,SignalPositionStateRecord,roots.c.sent_at,roots.c.exchange)
                .join(roots,roots.c.signal_id==SignalRecord.id).outerjoin(SignalMonitorStateRecord,SignalMonitorStateRecord.signal_id==SignalRecord.id)
                .outerjoin(SignalPositionStateRecord,SignalPositionStateRecord.signal_id==SignalRecord.id)
                .order_by(desc(roots.c.sent_at),desc(SignalRecord.id)).limit(PAGE_SIZE).offset((page-1)*PAGE_SIZE))).all()
            items=[]
            for sig,monitor,position,sent_at,exchange in rows:
                p=_projection(sig,monitor,position); items.append(DestinationTradeHistoryItem(p.signal_id,p.canonical_symbol,p.direction,
                    p.entry_type,p.source_label,exchange.value,_aware_utc(sent_at).astimezone(TEHRAN),p.state,p.highest_tp,p.report_roi))
            return TradeHistoryPage(tuple(items),page,max(1,(total+PAGE_SIZE-1)//PAGE_SIZE),total)


class ReportingService:
    def __init__(self,repo): self.repo=repo
    async def summary(self,flt): return await self.repo.summary(flt)
    async def history(self,flt,page=1): return await self.repo.history(flt,page)
    async def signalist_summary(self,owner_signalist_id,flt): return await self.repo.signalist_summary(owner_signalist_id,flt)
    async def signalist_history(self,owner_signalist_id,flt,page=1): return await self.repo.signalist_history(owner_signalist_id,flt,page)
    async def destination_summary(self,flt): return await self.repo.destination_summary(flt)
    async def destination_history(self,flt,page=1): return await self.repo.destination_history(flt,page)
    async def destination_exists(self,destination_id): return await self.repo.destination_exists(destination_id)
