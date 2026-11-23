"""Durable Limit-entry monitoring and activation."""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from chainura.domain.monitoring import MonitorStage, NotificationKind, NotificationStatus
from chainura.domain.signal import (SignalEntryType, SignalLifecycleStatus, SignalMode,
    limit_entry_reached)
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.models import (LimitEntryTriggerRecord, LimitEntryTriggerSourcePriceRecord,
    SignalExchangeSymbolRecord, SignalMonitorStateRecord, SignalPositionStateRecord,
    SignalRecord, SignalistNotificationRecord, SignalistRecord)
from chainura.persistence.publication_repository import SqlAlchemyPublicationRepository

LOGGER=logging.getLogger("chainura.limit_orders")

@dataclass(frozen=True,slots=True)
class LimitMonitorWork:
    signal_id:int;signalist_id:int;mode:SignalMode;canonical_symbol:str
    selected_exchange:object|None;direction:object;entry_price:Decimal;leverage:int
    take_profits:tuple[Decimal,...];stop_loss:Decimal;price_source_type:object
    exchange_symbols:tuple[tuple[object,str],...];created_at:datetime

class LimitOrderService:
    """Owns irreversible entry triggers and the atomic activation boundary."""
    def __init__(self,sessions:async_sessionmaker,market=None,
                 clock=lambda:datetime.now(timezone.utc),notification_wake=None,
                 publication_wake=None,monitoring_wake=None):
        self.sessions=sessions;self.market=market;self.clock=clock
        self.notification_wake=notification_wake;self.publication_wake=publication_wake
        self.monitoring_wake=monitoring_wake

    async def load_work(self,limit=100):
        async with self.sessions() as s:
            rows=(await s.scalars(select(SignalRecord).where(
                SignalRecord.entry_type==SignalEntryType.LIMIT,
                SignalRecord.status==SignalLifecycleStatus.PENDING_ENTRY
            ).order_by(SignalRecord.id).limit(limit))).all()
            result=[]
            for row in rows:
                mappings=tuple((x.exchange,x.native_symbol) for x in (await s.scalars(
                    select(SignalExchangeSymbolRecord).where(
                        SignalExchangeSymbolRecord.signal_id==row.id).order_by(
                        SignalExchangeSymbolRecord.exchange))).all())
                result.append(LimitMonitorWork(row.id,row.signalist_id,row.mode,
                    row.canonical_symbol,row.selected_exchange,row.direction,row.entry_price,
                    row.leverage,(),row.stop_loss,row.price_source_type,mappings,row.created_at))
            return tuple(result)

    async def run_cycle(self,limit=100,concurrency=8):
        work=await self.load_work(limit)
        decisions=await self.market.snapshot(work) if self.market is not None else {}
        semaphore=asyncio.Semaphore(concurrency)
        async def one(item):
            decision=decisions.get(item.signal_id)
            if decision is None:return False
            async with semaphore:
                return await self.observe(item.signal_id,decision.reference_price,
                    decision.price_source_type,decision.source_prices,decision.observed_at)
        results=await asyncio.gather(*(one(item) for item in work),return_exceptions=True)
        for item,result in zip(work,results):
            if isinstance(result,BaseException):
                LOGGER.warning("Limit trigger isolated signal_id=%s category=%s",
                               item.signal_id,type(result).__name__)
        return sum(result is True for result in results)

    async def observe(self,signal_id:int,reference_price:Decimal,source_type,
                      source_prices:tuple[tuple[object,Decimal],...],observed_at:datetime)->bool:
        """CAS state, trigger facts, source prices and interaction in one commit."""
        now=self.clock()
        async with self.sessions() as s,s.begin():
            signal=await s.get(SignalRecord,signal_id)
            if (signal is None or signal.entry_type is not SignalEntryType.LIMIT or
                    signal.status is not SignalLifecycleStatus.PENDING_ENTRY or
                    not limit_entry_reached(signal.direction,reference_price,signal.entry_price)):
                return False
            changed=(await s.execute(update(SignalRecord).where(
                SignalRecord.id==signal_id,
                SignalRecord.status==SignalLifecycleStatus.PENDING_ENTRY,
                SignalRecord.entry_type==SignalEntryType.LIMIT).values(
                status=(SignalLifecycleStatus.ACTIVE if signal.auto_activate_limit
                        else SignalLifecycleStatus.ENTRY_TRIGGERED),updated_at=now))).rowcount
            if changed!=1:return False
            s.add(LimitEntryTriggerRecord(signal_id=signal_id,
                requested_entry=signal.entry_price,observed_reference_price=reference_price,
                price_source_type=source_type,occurred_at=observed_at,created_at=now))
            s.add_all(LimitEntryTriggerSourcePriceRecord(signal_id=signal_id,exchange=e,price=p)
                      for e,p in source_prices)
            s.add(SignalistNotificationRecord(kind=NotificationKind.LIMIT_TRIGGER,
                limit_trigger_signal_id=signal_id,signalist_id=signal.signalist_id,
                status=NotificationStatus.PENDING,created_at=now,updated_at=now))
            if signal.auto_activate_limit:
                await self._initialize_activation(s, signal, now)
            await s.flush() # any conflict aborts the whole transaction; never swallowed
        if self.notification_wake:self.notification_wake()
        if signal.auto_activate_limit:self._wake_activation()
        return True

    async def activate(self,signal_id:int,signalist_id:int)->bool:
        """Activate once and freeze destination work from SENT admission rows only."""
        now=self.clock()
        async with self.sessions() as s,s.begin():
            signal=await s.get(SignalRecord,signal_id);actor=await s.get(SignalistRecord,signalist_id)
            if signal is None or signal.signalist_id!=signalist_id or signal.entry_type is not SignalEntryType.LIMIT:
                return False
            if signal.status is SignalLifecycleStatus.ACTIVE:
                return True
            trigger=await s.get(LimitEntryTriggerRecord,signal_id)
            if (signal.auto_activate_limit or actor is None or actor.status is not SignalistStatus.ACTIVE or trigger is None or
                    signal.status is not SignalLifecycleStatus.ENTRY_TRIGGERED):return False
            changed=(await s.execute(update(SignalRecord).where(SignalRecord.id==signal_id,
                SignalRecord.status==SignalLifecycleStatus.ENTRY_TRIGGERED,
                SignalRecord.entry_type==SignalEntryType.LIMIT).values(
                status=SignalLifecycleStatus.ACTIVE,updated_at=now))).rowcount
            if changed!=1:return False
            await self._initialize_activation(s, signal, now)
            await s.flush() # position/monitor/publication intent are one boundary
        self._wake_activation()
        return True

    async def _initialize_activation(self, session, signal, now):
        session.add(SignalPositionStateRecord(signal_id=signal.id,remaining_fraction=Decimal("1"),
            realized_weighted_roi=Decimal("0"),risk_free_used=False,risk_free_active=False,
            save_profit_50_used=False,effective_stop_price=signal.stop_loss,is_closed=False,
            version=1,created_at=now,updated_at=now))
        session.add(SignalMonitorStateRecord(signal_id=signal.id,stage=MonitorStage.PRE_TP1,
            next_tp_index=1,highest_tp_index=0,created_at=now,updated_at=now))
        await SqlAlchemyPublicationRepository(session).queue_limit_initials(signal, now)

    def _wake_activation(self):
        if self.publication_wake:self.publication_wake()
        if self.monitoring_wake:self.monitoring_wake()

class LimitMonitoringWorker:
    def __init__(self,service:LimitOrderService,interval=5,batch_size=100,concurrency=8):
        self.service=service;self.interval=interval;self.batch_size=batch_size;self.concurrency=concurrency
        self._wake=asyncio.Event();self._stop=asyncio.Event();self.task=None
    def wake(self):self._wake.set()
    async def start(self):
        if self.task is None:self.task=asyncio.create_task(self._run())
    async def _run(self):
        while not self._stop.is_set():
            try:await self.service.run_cycle(self.batch_size,self.concurrency)
            except Exception as exc:LOGGER.warning("Limit monitor cycle failed category=%s",type(exc).__name__)
            self._wake.clear()
            try:await asyncio.wait_for(self._wake.wait(),self.interval)
            except asyncio.TimeoutError:pass
    async def stop(self):
        self._stop.set();self._wake.set()
        if self.task:await self.task
