"""Durable Phase 6A monitoring application boundary."""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Protocol

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from chainura.domain.monitoring import *
from chainura.domain.signal import SignalMode, SignalLifecycleStatus
from chainura.domain.signalist import SignalistStatus
LOGGER=logging.getLogger("chainura.monitoring")

from chainura.persistence.models import (SignalRecord,SignalTakeProfitRecord,
 SignalExchangeSymbolRecord,SignalInitialPriceRecord,SignalMonitorStateRecord,
 SignalEventRecord,SignalEventSourcePriceRecord,SignalUpdateCandidateRecord,
 BreakevenPromptRecord,SignalistNotificationRecord,SignalistRecord,UpdatePublicationRequestRecord,
 DestinationTradeLockRecord)


@dataclass(frozen=True,slots=True)
class BreakevenSettings:
    min_open_seconds:int=300
    favorable_move_percent:Decimal=Decimal("1.00")
    execution_tolerance_percent:Decimal=Decimal("0.10")
    rearm_distance_percent:Decimal=Decimal("0.20")
    prompt_cooldown_seconds:int=900


@dataclass(frozen=True,slots=True)
class MonitorWork:
    signal_id:int
    signalist_id:int
    mode:SignalMode
    canonical_symbol:str
    selected_exchange:object|None
    direction:object
    entry_price:Decimal
    leverage:int
    take_profits:tuple[Decimal,...]
    stop_loss:Decimal
    price_source_type:object
    exchange_symbols:tuple[tuple[object,str],...]
    created_at:datetime


class BatchMarketProvider(Protocol):
    async def snapshot(self, signals:tuple[MonitorWork,...]) -> dict[int,MarketDecision]:...


class MonitoringService:
    def __init__(self,sessions:async_sessionmaker,market:BatchMarketProvider|None=None,
                 settings:BreakevenSettings=BreakevenSettings(),clock=lambda:datetime.now(timezone.utc),notification_wake:Callable[[],None]|None=None,update_publication_wake:Callable[[],None]|None=None):
        self.sessions=sessions;self.market=market;self.settings=settings;self.clock=clock;self.notification_wake=notification_wake;self.update_publication_wake=update_publication_wake

    async def reconcile(self)->None:
        """Enroll valid legacy signals once without deriving historical events."""
        now=self.clock()
        async with self.sessions() as s, s.begin():
            rows=(await s.scalars(select(SignalRecord).where(
                SignalRecord.status==SignalLifecycleStatus.ACTIVE,
                ~SignalRecord.id.in_(select(SignalMonitorStateRecord.signal_id))))).all()
            for row in rows:
                maps=(await s.scalars(select(SignalExchangeSymbolRecord).where(SignalExchangeSymbolRecord.signal_id==row.id))).all()
                prices=(await s.scalars(select(SignalInitialPriceRecord).where(SignalInitialPriceRecord.signal_id==row.id))).all()
                valid=(row.mode is SignalMode.COMPOSITE and len(maps)==3 and len(prices)>=2) or (row.mode is SignalMode.SINGLE_EXCHANGE and len(maps)==len(prices)==1)
                s.add(SignalMonitorStateRecord(signal_id=row.id,stage=MonitorStage.PRE_TP1 if valid else MonitorStage.DISABLED_LEGACY,disabled_reason=None if valid else "missing_phase5_snapshot",created_at=now,updated_at=now))

    async def load_work(self,limit:int)->tuple[MonitorWork,...]:
        async with self.sessions() as s:
            rows=(await s.execute(select(SignalRecord,SignalMonitorStateRecord).join(SignalMonitorStateRecord).where(
                SignalRecord.status==SignalLifecycleStatus.ACTIVE,
                SignalMonitorStateRecord.stage.in_((MonitorStage.PRE_TP1,MonitorStage.POST_TP1))).order_by(SignalMonitorStateRecord.last_checked_at,SignalRecord.id).limit(limit))).all()
            result=[]
            for signal,_ in rows:
                tps=(await s.scalars(select(SignalTakeProfitRecord).where(SignalTakeProfitRecord.signal_id==signal.id).order_by(SignalTakeProfitRecord.position))).all()
                maps=(await s.scalars(select(SignalExchangeSymbolRecord).where(SignalExchangeSymbolRecord.signal_id==signal.id))).all()
                result.append(MonitorWork(signal.id,signal.signalist_id,signal.mode,signal.canonical_symbol,signal.selected_exchange,signal.direction,signal.entry_price,signal.leverage,tuple(x.price for x in tps),signal.stop_loss,signal.price_source_type,tuple((x.exchange,x.native_symbol) for x in maps),signal.created_at))
            return tuple(result)

    async def evaluate(self,work:MonitorWork,market:MarketDecision)->bool:
        now=market.observed_at
        async with self.sessions() as s, s.begin():
            signal=await s.get(SignalRecord,work.signal_id)
            if signal is None or signal.status is not SignalLifecycleStatus.ACTIVE:return False
            state=await s.get(SignalMonitorStateRecord,work.signal_id)
            if state is None or state.stage not in (MonitorStage.PRE_TP1,MonitorStage.POST_TP1):return False
            version=state.version
            decisions=evaluate_price(direction=work.direction,entry=work.entry_price,leverage=work.leverage,take_profits=work.take_profits,stop_loss=work.stop_loss,next_tp_index=state.next_tp_index,stage=state.stage,market=market)
            if decisions:
                created=[]
                for decision in decisions:
                    event=SignalEventRecord(signal_id=work.signal_id,kind=decision.kind,tp_index=decision.tp_index,threshold_price=decision.threshold_price,observed_reference_price=decision.observed_reference_price,roi_basis_price=decision.roi_basis_price,leveraged_roi=decision.leveraged_roi,price_source_type=market.price_source_type,occurred_at=now,created_at=now)
                    s.add(event);await s.flush();created.append(event)
                    s.add_all([SignalEventSourcePriceRecord(event_id=event.id,exchange=e,price=p) for e,p in market.source_prices])
                latest=created[-1];decision=decisions[-1]
                actor=await s.get(SignalistRecord,work.signalist_id)
                actionable=actor is not None and actor.status is SignalistStatus.ACTIVE
                await s.execute(update(SignalUpdateCandidateRecord).where(SignalUpdateCandidateRecord.signal_id==work.signal_id,SignalUpdateCandidateRecord.status.in_((CandidateStatus.AWAITING_NOTIFICATION,CandidateStatus.AWAITING_DECISION))).values(status=CandidateStatus.SUPERSEDED,superseded_by_event_id=latest.id,updated_at=now))
                await s.execute(update(BreakevenPromptRecord).where(BreakevenPromptRecord.signal_id==work.signal_id,BreakevenPromptRecord.status.in_((PromptStatus.PENDING_NOTIFICATION,PromptStatus.AWAITING_RESPONSE))).values(status=PromptStatus.SUPERSEDED,expired_at=now,updated_at=now))
                candidate=SignalUpdateCandidateRecord(signal_id=work.signal_id,event_id=latest.id,signalist_id=work.signalist_id,status=CandidateStatus.AWAITING_NOTIFICATION if actionable else CandidateStatus.INELIGIBLE_INACTIVE,created_at=now,updated_at=now)
                s.add(candidate);await s.flush()
                if actionable:s.add(SignalistNotificationRecord(kind=NotificationKind.EVENT,candidate_id=candidate.id,signalist_id=work.signalist_id,status=NotificationStatus.PENDING,created_at=now,updated_at=now))
                if decision.kind is SignalEventKind.TP_HIT:
                    state.highest_tp_index=decision.tp_index;state.next_tp_index=decision.tp_index+1;state.current_result_kind=ResultKind.WIN
                    state.stage=MonitorStage.COMPLETED_TARGETS if decision.tp_index==len(work.take_profits) else MonitorStage.POST_TP1
                else:state.current_result_kind=ResultKind.LOSS;state.stage=MonitorStage.COMPLETED_SL
                state.current_result_price=decision.threshold_price;state.current_leveraged_roi=decision.leveraged_roi
                if state.stage in (MonitorStage.COMPLETED_TARGETS,MonitorStage.COMPLETED_SL):
                    state.completed_at=now
                    await s.execute(delete(DestinationTradeLockRecord).where(
                        DestinationTradeLockRecord.signal_id==work.signal_id))
            elif state.stage is MonitorStage.PRE_TP1:
                await self._breakeven_state(s,state,work,market,now)
            state.last_checked_at=now;state.last_reference_price=market.reference_price;state.updated_at=now;state.version=version+1
            return True

    async def _breakeven_state(self,s,state,work,market,now):
        cfg=self.settings
        if state.favorable_threshold_reached_at is None and favorable_percent(work.direction,work.entry_price,market.reference_price)>=cfg.favorable_move_percent:state.favorable_threshold_reached_at=now
        old_inside=state.currently_inside_breakeven_zone
        distance=entry_distance_percent(work.entry_price,market.reference_price)
        inside=distance<=cfg.execution_tolerance_percent
        if state.breakeven_armed_at is None and state.favorable_threshold_reached_at is not None and now-work.created_at>=timedelta(seconds=cfg.min_open_seconds):state.breakeven_armed_at=now
        if distance>=cfg.rearm_distance_percent and state.last_breakeven_prompt_at is not None:
            state.breakeven_rearm_reached_at=state.breakeven_rearm_reached_at or now
            await s.execute(update(BreakevenPromptRecord).where(BreakevenPromptRecord.signal_id==work.signal_id,BreakevenPromptRecord.status.in_((PromptStatus.PENDING_NOTIFICATION,PromptStatus.AWAITING_RESPONSE))).values(status=PromptStatus.EXPIRED,expired_at=now,updated_at=now))
        cooldown=state.last_breakeven_prompt_at is None or now-state.last_breakeven_prompt_at>=timedelta(seconds=cfg.prompt_cooldown_seconds)
        rearmed=state.last_breakeven_prompt_at is None or state.breakeven_rearm_reached_at is not None
        if state.breakeven_armed_at and inside and not old_inside and cooldown and rearmed:
            state.breakeven_cycle+=1;state.last_breakeven_prompt_at=now;state.breakeven_rearm_reached_at=None
            prompt=BreakevenPromptRecord(signal_id=work.signal_id,cycle_number=state.breakeven_cycle,status=PromptStatus.PENDING_NOTIFICATION,observed_price_at_creation=market.reference_price,created_at=now,updated_at=now);s.add(prompt);await s.flush()
            actor=await s.get(SignalistRecord,work.signalist_id)
            if actor and actor.status is SignalistStatus.ACTIVE:s.add(SignalistNotificationRecord(kind=NotificationKind.BREAKEVEN_PROMPT,prompt_id=prompt.id,signalist_id=work.signalist_id,status=NotificationStatus.PENDING,created_at=now,updated_at=now))
        state.currently_inside_breakeven_zone=inside

    async def run_cycle(self,limit:int=100,concurrency:int=8)->int:
        if self.market is None:return 0
        work=await self.load_work(limit);snapshots=await self.market.snapshot(work);sem=asyncio.Semaphore(concurrency)
        async def one(item):
            if item.signal_id not in snapshots:return False
            async with sem:
                try:
                    return await self.evaluate(item,snapshots[item.signal_id])
                except Exception as exc:
                    LOGGER.warning("monitor signal evaluation failed signal_id=%s category=%s",item.signal_id,type(exc).__name__)
                    return False
        try:
            return sum(await asyncio.gather(*(one(x) for x in work)))
        except Exception as exc:
            LOGGER.warning("monitor cycle failed category=%s",type(exc).__name__)
            return 0

    async def decide_candidate(self,candidate_id:int,signalist_id:int,approve:bool)->CandidateStatus:
        now=self.clock()
        async with self.sessions() as s,s.begin():
            row=await s.get(SignalUpdateCandidateRecord,candidate_id);actor=await s.get(SignalistRecord,signalist_id)
            signal=await s.get(SignalRecord,row.signal_id) if row else None
            if row is None or signal is None or signal.status is not SignalLifecycleStatus.ACTIVE or row.signalist_id!=signalist_id or actor is None or actor.status is not SignalistStatus.ACTIVE:raise ValueError("candidate is not actionable")
            wanted=CandidateStatus.APPROVED if approve else CandidateStatus.DECLINED
            if row.status is wanted:return wanted
            if row.status is not CandidateStatus.AWAITING_DECISION:raise ValueError("candidate is not actionable")
            row.status=wanted;row.approved_at=now if approve else None;row.declined_at=None if approve else now;row.updated_at=now
            if approve:
                s.add(UpdatePublicationRequestRecord(candidate_id=row.id,signal_id=row.signal_id,event_id=row.event_id,status=__import__('chainura.domain.publication',fromlist=['PublicationRequestStatus']).PublicationRequestStatus.PENDING,created_at=now,updated_at=now))
            result=wanted
        if approve and self.update_publication_wake:self.update_publication_wake()
        return result

    async def decline_breakeven(self,prompt_id:int,signalist_id:int,cycle:int|None=None)->None:
        now=self.clock()
        async with self.sessions() as s,s.begin():
            prompt=await s.get(BreakevenPromptRecord,prompt_id);signal=await s.get(SignalRecord,prompt.signal_id) if prompt else None;actor=await s.get(SignalistRecord,signalist_id)
            if not prompt or not signal or signal.status is not SignalLifecycleStatus.ACTIVE or signal.signalist_id!=signalist_id or not actor or actor.status is not SignalistStatus.ACTIVE:raise ValueError("prompt is not actionable")
            if cycle and prompt.cycle_number!=cycle:raise ValueError("prompt is not actionable")
            if prompt.status is PromptStatus.DECLINED:return
            if prompt.status is not PromptStatus.AWAITING_RESPONSE:raise ValueError("prompt is not actionable")
            prompt.status=PromptStatus.DECLINED;prompt.declined_at=now;prompt.updated_at=now

    async def _prompt_work(self,prompt_id:int,signalist_id:int,cycle:int|None=None)->MonitorWork:
        async with self.sessions() as s:
            prompt=await s.get(BreakevenPromptRecord,prompt_id);signal=await s.get(SignalRecord,prompt.signal_id) if prompt else None;state=await s.get(SignalMonitorStateRecord,prompt.signal_id) if prompt else None;actor=await s.get(SignalistRecord,signalist_id)
            if not prompt or not signal or signal.status is not SignalLifecycleStatus.ACTIVE or not state or signal.signalist_id!=signalist_id or not actor or actor.status is not SignalistStatus.ACTIVE or prompt.status is not PromptStatus.AWAITING_RESPONSE or state.stage is not MonitorStage.PRE_TP1:raise ValueError("prompt is not actionable")
            if cycle and prompt.cycle_number!=cycle:raise ValueError("prompt is not actionable")
            tps=(await s.scalars(select(SignalTakeProfitRecord).where(SignalTakeProfitRecord.signal_id==signal.id).order_by(SignalTakeProfitRecord.position))).all()
            maps=(await s.scalars(select(SignalExchangeSymbolRecord).where(SignalExchangeSymbolRecord.signal_id==signal.id))).all()
            return MonitorWork(signal.id,signal.signalist_id,signal.mode,signal.canonical_symbol,signal.selected_exchange,signal.direction,signal.entry_price,signal.leverage,tuple(x.price for x in tps),signal.stop_loss,signal.price_source_type,tuple((x.exchange,x.native_symbol) for x in maps),signal.created_at)

    async def register_breakeven_with_fresh_price(self,prompt_id:int,signalist_id:int,cycle:int|None=None)->bool:
        if self.market is None:raise ValueError("market snapshot is unavailable")
        work=await self._prompt_work(prompt_id,signalist_id,cycle)
        snapshots=await self.market.snapshot((work,))
        market=snapshots.get(work.signal_id)
        if market is None:raise ValueError("market snapshot is unavailable")
        result=await self.register_breakeven(prompt_id,signalist_id,market,cycle)
        if result and self.notification_wake:self.notification_wake()
        return result

    async def register_breakeven(self,prompt_id:int,signalist_id:int,market:MarketDecision,cycle:int|None=None)->bool:
        """CAS a prompt after its caller obtained a fresh market decision."""
        now=market.observed_at
        async with self.sessions() as s,s.begin():
            prompt=await s.get(BreakevenPromptRecord,prompt_id);signal=await s.get(SignalRecord,prompt.signal_id) if prompt else None;actor=await s.get(SignalistRecord,signalist_id);state=await s.get(SignalMonitorStateRecord,prompt.signal_id) if prompt else None
            if not prompt or not signal or signal.status is not SignalLifecycleStatus.ACTIVE or not state or signal.signalist_id!=signalist_id or not actor or actor.status is not SignalistStatus.ACTIVE:raise ValueError("prompt is not actionable")
            if cycle and prompt.cycle_number!=cycle:raise ValueError("prompt is not actionable")
            if prompt.status is PromptStatus.ACCEPTED:return True
            if prompt.status is not PromptStatus.AWAITING_RESPONSE or state.stage is not MonitorStage.PRE_TP1:raise ValueError("prompt is not actionable")
            distance=entry_distance_percent(signal.entry_price,market.reference_price)
            if distance>self.settings.execution_tolerance_percent:
                if distance>=self.settings.rearm_distance_percent:prompt.status=PromptStatus.EXPIRED;prompt.expired_at=now;state.breakeven_rearm_reached_at=now
                prompt.updated_at=now;return False
            event=SignalEventRecord(signal_id=signal.id,kind=SignalEventKind.BREAKEVEN,tp_index=None,threshold_price=signal.entry_price,observed_reference_price=market.reference_price,roi_basis_price=signal.entry_price,leveraged_roi=Decimal("0"),price_source_type=market.price_source_type,occurred_at=now,created_at=now);s.add(event);await s.flush();s.add_all([SignalEventSourcePriceRecord(event_id=event.id,exchange=e,price=p) for e,p in market.source_prices])
            prompt.status=PromptStatus.ACCEPTED;prompt.accepted_at=now;prompt.updated_at=now;state.stage=MonitorStage.COMPLETED_BREAKEVEN;state.current_result_kind=ResultKind.NEUTRAL;state.current_result_price=signal.entry_price;state.current_leveraged_roi=Decimal("0");state.completed_at=now;state.updated_at=now;state.version+=1
            await s.execute(delete(DestinationTradeLockRecord).where(
                DestinationTradeLockRecord.signal_id==signal.id))
            candidate=SignalUpdateCandidateRecord(signal_id=signal.id,event_id=event.id,signalist_id=signalist_id,status=CandidateStatus.AWAITING_NOTIFICATION,created_at=now,updated_at=now);s.add(candidate);await s.flush();s.add(SignalistNotificationRecord(kind=NotificationKind.EVENT,candidate_id=candidate.id,signalist_id=signalist_id,status=NotificationStatus.PENDING,created_at=now,updated_at=now));return True


class MonitoringWorker:
    def __init__(self,service:MonitoringService,interval:float=5,batch_size:int=100,concurrency:int=8):
        self.service=service;self.interval=interval;self.batch_size=batch_size;self.concurrency=concurrency;self._wake=asyncio.Event();self._stop=asyncio.Event();self.task=None
    def wake(self):self._wake.set()
    async def start(self):
        if self.task is None:self.task=asyncio.create_task(self._run())
    async def _run(self):
        while not self._stop.is_set():
            try:await self.service.run_cycle(self.batch_size,self.concurrency)
            except Exception as exc:
                LOGGER.warning("monitor worker cycle failed category=%s",type(exc).__name__)
            self._wake.clear()
            try:await asyncio.wait_for(self._wake.wait(),self.interval)
            except asyncio.TimeoutError:pass
    async def stop(self):
        self._stop.set();self._wake.set()
        if self.task:await self.task
