"""Durable Limit-entry trigger and activation application service."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from chainura.domain.signal import (SignalEntryType, SignalLifecycleStatus,
    limit_entry_reached)
from chainura.domain.signalist import SignalistStatus
from chainura.domain.monitoring import MonitorStage
from chainura.persistence.models import (InitialPublicationRequestRecord,
    LimitEntryTriggerRecord, LimitEntryTriggerSourcePriceRecord, SignalMonitorStateRecord,
    SignalPositionStateRecord, SignalRecord, SignalistRecord)

class LimitOrderService:
    """All lifecycle transitions use SQLite compare-and-swap operations."""
    def __init__(self, sessions, clock=lambda: datetime.now(timezone.utc),
                 notification_wake=None, publication_wake=None, monitoring_wake=None):
        self.sessions=sessions;self.clock=clock;self.notification_wake=notification_wake
        self.publication_wake=publication_wake;self.monitoring_wake=monitoring_wake

    async def observe(self, signal_id: int, reference_price: Decimal, source_type,
                      source_prices: tuple[tuple[object, Decimal], ...], observed_at: datetime) -> bool:
        """Persist one trigger and the irreversible state transition atomically."""
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
                status=SignalLifecycleStatus.ENTRY_TRIGGERED,updated_at=self.clock()))).rowcount
            if changed != 1:return False
            s.add(LimitEntryTriggerRecord(signal_id=signal_id,requested_entry=signal.entry_price,
                observed_reference_price=reference_price,price_source_type=source_type,
                occurred_at=observed_at,created_at=self.clock()))
            s.add_all(LimitEntryTriggerSourcePriceRecord(signal_id=signal_id,exchange=e,price=p)
                      for e,p in source_prices)
            try:await s.flush()
            except IntegrityError:return False
        if self.notification_wake:self.notification_wake()
        return True

    async def activate(self, signal_id: int, signalist_id: int) -> bool:
        """Activate exactly once without revalidating a price that may have moved."""
        now=self.clock()
        async with self.sessions() as s,s.begin():
            signal=await s.get(SignalRecord,signal_id);actor=await s.get(SignalistRecord,signalist_id)
            if signal is None or signal.signalist_id!=signalist_id or signal.entry_type is not SignalEntryType.LIMIT:
                return False
            if signal.status is SignalLifecycleStatus.ACTIVE:
                return True
            trigger=await s.get(LimitEntryTriggerRecord,signal_id)
            if (actor is None or actor.status is not SignalistStatus.ACTIVE or trigger is None or
                    signal.status is not SignalLifecycleStatus.ENTRY_TRIGGERED):return False
            changed=(await s.execute(update(SignalRecord).where(SignalRecord.id==signal_id,
                SignalRecord.status==SignalLifecycleStatus.ENTRY_TRIGGERED).values(
                status=SignalLifecycleStatus.ACTIVE,updated_at=now))).rowcount
            if changed!=1:return False
            s.add(SignalPositionStateRecord(signal_id=signal_id,remaining_fraction=Decimal('1'),
                realized_weighted_roi=Decimal('0'),risk_free_used=False,risk_free_active=False,
                save_profit_50_used=False,effective_stop_price=signal.stop_loss,is_closed=False,
                version=1,created_at=now,updated_at=now))
            s.add(SignalMonitorStateRecord(signal_id=signal_id,stage=MonitorStage.PRE_TP1,
                next_tp_index=1,highest_tp_index=0,created_at=now,updated_at=now))
            # Activation reuses the normal image/caption pipeline. The planner is
            # responsible for filtering to definitely-sent pending parents.
            s.add(InitialPublicationRequestRecord(signal_id=signal_id,created_at=now,updated_at=now))
        if self.publication_wake:self.publication_wake()
        if self.monitoring_wake:self.monitoring_wake()
        return True
