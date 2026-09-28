"""Durable Phase 6A monitoring application boundary."""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from chainura.domain.monitoring import *
from chainura.domain.signal import SignalMode, SignalLifecycleStatus
from chainura.domain.destination import Exchange
from chainura.domain.signalist import SignalistStatus
LOGGER=logging.getLogger("chainura.monitoring")

from chainura.persistence.models import (SignalRecord,SignalTakeProfitRecord,
 SignalExchangeSymbolRecord,SignalInitialPriceRecord,SignalMonitorStateRecord,
 SignalEventRecord,SignalEventSourcePriceRecord,SignalUpdateCandidateRecord,
 BreakevenPromptRecord,SignalistNotificationRecord,SignalistRecord,UpdatePublicationRequestRecord,
 DestinationTradeLockRecord)
from chainura.persistence.models import SignalPositionStateRecord,SignalPositionActionRecord
from chainura.domain.position import PositionState,PositionActionKind,apply_position_action,InvalidPositionTransition
from chainura.domain.publication import PublicationRequestStatus


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


CURRENT_INTERACTION_VERSION = 2
LEGACY_TERMINAL_INTERACTION_VERSION = 3
TERMINAL_MONITOR_STAGES = (MonitorStage.COMPLETED_TARGETS, MonitorStage.COMPLETED_SL,
    MonitorStage.COMPLETED_BREAKEVEN, MonitorStage.COMPLETED_FULL_CLOSE,
    MonitorStage.COMPLETED_RISK_FREE)


@dataclass(frozen=True, slots=True)
class CompatibilitySummary:
    open_position_states_created:int = 0
    legacy_tp_interactions_refreshed:int = 0
    missing_tp_notification_intents_repaired:int = 0
    legacy_breakeven_prompts_superseded:int = 0
    inactive_owner_final_positions_terminalized:int = 0
    stale_trade_locks_removed:int = 0
    disabled_legacy_active_signals:int = 0


class BatchMarketProvider(Protocol):
    async def snapshot(self, signals:tuple[MonitorWork,...]) -> dict[int,MarketDecision]:...


class MonitoringService:
    def __init__(self,sessions:async_sessionmaker,market:BatchMarketProvider|None=None,
                 settings:BreakevenSettings=BreakevenSettings(),clock=lambda:datetime.now(timezone.utc),notification_wake:Callable[[],None]|None=None,update_publication_wake:Callable[[],None]|None=None):
        self.sessions=sessions;self.market=market;self.settings=settings;self.clock=clock;self.notification_wake=notification_wake;self.update_publication_wake=update_publication_wake

    async def reconcile(self)->CompatibilitySummary:
        """Enroll valid legacy signals once without deriving historical events."""
        now=self.clock()
        async with self.sessions() as s, s.begin():
            rows=(await s.scalars(select(SignalRecord).where(
                SignalRecord.status==SignalLifecycleStatus.ACTIVE,
                ~SignalRecord.id.in_(select(SignalMonitorStateRecord.signal_id))))).all()
            for row in rows:
                maps=(await s.scalars(select(SignalExchangeSymbolRecord).where(SignalExchangeSymbolRecord.signal_id==row.id))).all()
                prices=(await s.scalars(select(SignalInitialPriceRecord).where(SignalInitialPriceRecord.signal_id==row.id))).all()
                mapped={item.exchange for item in maps}
                valid=(row.mode is SignalMode.COMPOSITE and {Exchange.OURBIT,Exchange.TOOBIT,Exchange.LBANK}.issubset(mapped) and len(prices)>=2) or (row.mode is SignalMode.SINGLE_EXCHANGE and len(maps)==len(prices)==1)
                s.add(SignalMonitorStateRecord(signal_id=row.id,stage=MonitorStage.PRE_TP1 if valid else MonitorStage.DISABLED_LEGACY,disabled_reason=None if valid else "missing_phase5_snapshot",created_at=now,updated_at=now))
            # Only genuinely open ACTIVE monitor states receive the new accounting
            # foundation. Historical terminal rows are deliberately untouched.
            open_ids=(await s.scalars(select(SignalMonitorStateRecord.signal_id).join(
                SignalRecord,SignalRecord.id==SignalMonitorStateRecord.signal_id).where(
                SignalRecord.status==SignalLifecycleStatus.ACTIVE,
                SignalMonitorStateRecord.stage.in_((MonitorStage.PRE_TP1,MonitorStage.POST_TP1)),
                ~SignalMonitorStateRecord.signal_id.in_(select(SignalPositionStateRecord.signal_id))))).all()
            positions_created=len(open_ids)
            if open_ids:
                signals=(await s.scalars(select(SignalRecord).where(SignalRecord.id.in_(open_ids)))).all()
                s.add_all([SignalPositionStateRecord(signal_id=row.id,
                    remaining_fraction=Decimal("1"),realized_weighted_roi=Decimal("0"),
                    risk_free_used=False,risk_free_active=False,save_profit_50_used=False,
                    effective_stop_price=row.stop_loss,is_closed=False,version=1,
                    created_at=now,updated_at=now) for row in signals])
            # Phase 4B retires (but deliberately does not delete) the legacy
            # contextual Breakeven workflow. Old buttons become stale after this
            # transaction and historical accepted/declined rows remain readable.
            prompt_ids=select(BreakevenPromptRecord.id).where(BreakevenPromptRecord.status.in_(
                (PromptStatus.PENDING_NOTIFICATION,PromptStatus.AWAITING_RESPONSE)))
            await s.execute(update(SignalistNotificationRecord).where(
                SignalistNotificationRecord.prompt_id.in_(prompt_ids),
                SignalistNotificationRecord.status.in_((NotificationStatus.PENDING,
                    NotificationStatus.SENDING,NotificationStatus.RETRY_WAIT))).values(
                        status=NotificationStatus.SUPERSEDED,updated_at=now))
            retired=(await s.execute(update(BreakevenPromptRecord).where(
                BreakevenPromptRecord.status.in_((PromptStatus.PENDING_NOTIFICATION,
                    PromptStatus.AWAITING_RESPONSE))).values(status=PromptStatus.SUPERSEDED,
                        expired_at=now,updated_at=now))).rowcount

            refreshed,repaired=await self._refresh_legacy_tp_interactions(s,now)
            terminalized=await self._terminalize_inactive_final_positions(s,now)
            stale=(await s.execute(delete(DestinationTradeLockRecord).where(
                DestinationTradeLockRecord.signal_id.in_(select(SignalRecord.id).outerjoin(
                    SignalMonitorStateRecord,SignalMonitorStateRecord.signal_id==SignalRecord.id).where(
                    (SignalRecord.status==SignalLifecycleStatus.CANCELLED)|
                    SignalMonitorStateRecord.stage.in_(TERMINAL_MONITOR_STAGES)))))).rowcount
            disabled=await s.scalar(select(func.count()).select_from(SignalMonitorStateRecord).join(
                SignalRecord,SignalRecord.id==SignalMonitorStateRecord.signal_id).where(
                SignalRecord.status==SignalLifecycleStatus.ACTIVE,
                SignalMonitorStateRecord.stage==MonitorStage.DISABLED_LEGACY)) or 0
            summary=CompatibilitySummary(positions_created,refreshed,repaired,retired,
                terminalized,stale,disabled)
        LOGGER.info("production compatibility reconciliation positions_created=%s tp_interactions_refreshed=%s tp_intents_repaired=%s breakeven_prompts_superseded=%s inactive_finals_terminalized=%s stale_locks_removed=%s disabled_legacy=%s",
            summary.open_position_states_created,summary.legacy_tp_interactions_refreshed,
            summary.missing_tp_notification_intents_repaired,
            summary.legacy_breakeven_prompts_superseded,
            summary.inactive_owner_final_positions_terminalized,
            summary.stale_trade_locks_removed,summary.disabled_legacy_active_signals)
        if (refreshed or repaired) and self.notification_wake:self.notification_wake()
        return summary

    async def _refresh_legacy_tp_interactions(self,s,now):
        rows=(await s.execute(select(SignalUpdateCandidateRecord,SignalEventRecord,SignalRecord,
            SignalistRecord,SignalistNotificationRecord).join(SignalEventRecord,
            SignalEventRecord.id==SignalUpdateCandidateRecord.event_id).join(SignalRecord,
            SignalRecord.id==SignalUpdateCandidateRecord.signal_id).join(SignalistRecord,
            SignalistRecord.id==SignalUpdateCandidateRecord.signalist_id).outerjoin(
            SignalistNotificationRecord,SignalistNotificationRecord.candidate_id==SignalUpdateCandidateRecord.id).where(
            SignalUpdateCandidateRecord.status.in_((CandidateStatus.AWAITING_NOTIFICATION,CandidateStatus.AWAITING_DECISION)),
            SignalUpdateCandidateRecord.superseded_by_event_id.is_(None),
            SignalEventRecord.kind==SignalEventKind.TP_HIT,
            SignalRecord.status==SignalLifecycleStatus.ACTIVE,
            SignalistRecord.status==SignalistStatus.ACTIVE,
            ~SignalUpdateCandidateRecord.id.in_(select(SignalPositionActionRecord.candidate_id).where(
                SignalPositionActionRecord.candidate_id.is_not(None)))))).all()
        refreshed=repaired=0
        for candidate,event,signal,actor,note in rows:
            legacy_terminal=await self._is_legacy_terminal_tp(s,candidate,event,signal)
            target_version=(LEGACY_TERMINAL_INTERACTION_VERSION if legacy_terminal
                            else CURRENT_INTERACTION_VERSION)
            if note is None:
                s.add(SignalistNotificationRecord(kind=NotificationKind.EVENT,candidate_id=candidate.id,
                    signalist_id=candidate.signalist_id,status=NotificationStatus.PENDING,
                    interaction_version=target_version,created_at=now,updated_at=now))
                repaired+=1
            elif ((legacy_terminal and note.interaction_version!=target_version) or
                    (not legacy_terminal and note.interaction_version<CURRENT_INTERACTION_VERSION)):
                note.interaction_version=target_version
                note.status=NotificationStatus.PENDING;note.attempt_count=0;note.retry_at=None
                note.telegram_message_id=None;note.sent_at=None;note.last_error_category=None;note.updated_at=now
                refreshed+=1
        return refreshed,repaired

    async def _is_legacy_terminal_tp(self,s,candidate,event=None,signal=None):
        event=event or await s.get(SignalEventRecord,candidate.event_id)
        signal=signal or await s.get(SignalRecord,candidate.signal_id)
        if not event or event.kind is not SignalEventKind.TP_HIT or not signal:return False
        monitor=await s.get(SignalMonitorStateRecord,signal.id)
        position=await s.get(SignalPositionStateRecord,signal.id)
        tp_count=await s.scalar(select(func.count()).select_from(SignalTakeProfitRecord).where(
            SignalTakeProfitRecord.signal_id==signal.id))
        action=await s.scalar(select(SignalPositionActionRecord.id).where(
            (SignalPositionActionRecord.candidate_id==candidate.id)|
            (SignalPositionActionRecord.event_id==event.id)))
        return bool(monitor and monitor.stage is MonitorStage.COMPLETED_TARGETS and
            position is None and action is None and event.tp_index==tp_count)

    async def reconcile_inactive_final_positions(self)->int:
        """Run lifecycle-only cleanup without requiring market monitoring."""
        async with self.sessions() as s,s.begin():
            return await self._terminalize_inactive_final_positions(s,self.clock())

    async def _terminalize_inactive_final_positions(self,s,now):
        rows=(await s.execute(select(SignalRecord,SignalMonitorStateRecord,SignalPositionStateRecord,
            SignalistRecord).join(SignalMonitorStateRecord).join(SignalPositionStateRecord).join(
            SignalistRecord,SignalistRecord.id==SignalRecord.signalist_id).where(
            SignalRecord.status==SignalLifecycleStatus.ACTIVE,
            SignalMonitorStateRecord.stage==MonitorStage.AWAITING_FINAL_TP_ACTION,
            SignalPositionStateRecord.is_closed.is_(False),
            SignalistRecord.status!=SignalistStatus.ACTIVE))).all()
        count=0
        for signal,monitor,position,_ in rows:
            tp_count=await s.scalar(select(func.count()).select_from(SignalTakeProfitRecord).where(
                SignalTakeProfitRecord.signal_id==signal.id))
            event=await s.scalar(select(SignalEventRecord).where(SignalEventRecord.signal_id==signal.id,
                SignalEventRecord.kind==SignalEventKind.TP_HIT,SignalEventRecord.tp_index==tp_count).order_by(
                SignalEventRecord.id.desc()).limit(1))
            if event is None or await s.scalar(select(SignalPositionActionRecord.id).where(
                SignalPositionActionRecord.event_id==event.id)) is not None:continue
            candidate=await s.scalar(select(SignalUpdateCandidateRecord).where(
                SignalUpdateCandidateRecord.event_id==event.id).order_by(SignalUpdateCandidateRecord.id.desc()).limit(1))
            if candidate and candidate.status not in (CandidateStatus.AWAITING_NOTIFICATION,
                    CandidateStatus.AWAITING_DECISION):
                # Pre-4B approved/declined rows are valid history, not missing
                # Phase-4 actions to reconstruct.
                continue
            final_roi=await self._apply_position(s,position,signal,event,candidate.id if candidate else None,
                PositionActionKind.FINAL_TP_CLOSE,now)
            if candidate and candidate.status in (CandidateStatus.AWAITING_NOTIFICATION,CandidateStatus.AWAITING_DECISION):
                candidate.status=CandidateStatus.INELIGIBLE_INACTIVE;candidate.updated_at=now
            monitor.stage=MonitorStage.COMPLETED_TARGETS;monitor.current_result_kind=self._result_kind(final_roi)
            monitor.current_result_price=event.roi_basis_price;monitor.current_leveraged_roi=final_roi
            monitor.completed_at=now;monitor.updated_at=now;monitor.version+=1
            await s.execute(delete(DestinationTradeLockRecord).where(DestinationTradeLockRecord.signal_id==signal.id))
            count+=1
        return count

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
            position=await s.get(SignalPositionStateRecord,work.signal_id)
            if position is None or position.is_closed:return False
            version=state.version
            decisions=evaluate_price(direction=work.direction,entry=work.entry_price,leverage=work.leverage,take_profits=work.take_profits,stop_loss=work.stop_loss,next_tp_index=state.next_tp_index,stage=state.stage,market=market,risk_free_active=position.risk_free_active,effective_stop_price=position.effective_stop_price)
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
                    state.stage=MonitorStage.AWAITING_FINAL_TP_ACTION if decision.tp_index==len(work.take_profits) else MonitorStage.POST_TP1
                else:
                    action=(PositionActionKind.RISK_FREE_HIT_CLOSE if decision.kind is SignalEventKind.RISK_FREE_HIT
                            else PositionActionKind.STOP_LOSS_CLOSE)
                    final_roi=await self._apply_position(s,position,signal,latest,None,action,now)
                    state.current_result_kind=self._result_kind(final_roi)
                    state.stage=(MonitorStage.COMPLETED_RISK_FREE if decision.kind is SignalEventKind.RISK_FREE_HIT
                                 else MonitorStage.COMPLETED_SL)
                state.current_result_price=decision.threshold_price;state.current_leveraged_roi=decision.leveraged_roi
                if state.stage in (MonitorStage.COMPLETED_SL,MonitorStage.COMPLETED_RISK_FREE):
                    state.current_leveraged_roi=final_roi
                    state.completed_at=now
                    await s.execute(delete(DestinationTradeLockRecord).where(
                        DestinationTradeLockRecord.signal_id==work.signal_id))
            # The old automatic Breakeven prompt generator is intentionally not
            # called. Entry is meaningful only for an explicitly active RF stop.
            state.last_checked_at=now;state.last_reference_price=market.reference_price;state.updated_at=now;state.version=version+1
            return True

    @staticmethod
    def _result_kind(value:Decimal)->ResultKind:
        return ResultKind.WIN if value>0 else ResultKind.LOSS if value<0 else ResultKind.NEUTRAL

    async def _apply_position(self,s,row,signal,event,candidate_id,action,now):
        current=PositionState(row.remaining_fraction,row.realized_weighted_roi,
            row.risk_free_used,row.risk_free_active,row.save_profit_50_used,
            row.effective_stop_price,row.is_closed,row.closed_at)
        transition=apply_position_action(current,action,leveraged_roi=event.leveraged_roi,
            entry_price=signal.entry_price,occurred_at=now)
        expected=row.version
        changed=(await s.execute(update(SignalPositionStateRecord).where(
            SignalPositionStateRecord.signal_id==signal.id,
            SignalPositionStateRecord.version==expected).values(
                remaining_fraction=transition.state.remaining_fraction,
                realized_weighted_roi=transition.state.realized_weighted_roi,
                risk_free_used=transition.state.risk_free_used,
                risk_free_active=transition.state.risk_free_active,
                save_profit_50_used=transition.state.save_profit_50_used,
                effective_stop_price=transition.state.effective_stop_price,
                is_closed=transition.state.is_closed,closed_at=transition.state.closed_at,
                version=expected+1,updated_at=now))).rowcount
        if changed != 1:raise ValueError("position decision is stale")
        ledger=SignalPositionActionRecord(signal_id=signal.id,event_id=event.id,
            candidate_id=candidate_id,action_kind=action,
            remaining_fraction_before=transition.remaining_fraction_before,
            closed_fraction=transition.closed_fraction,
            remaining_fraction_after=transition.state.remaining_fraction,
            execution_price=event.roi_basis_price,leveraged_roi=transition.leveraged_roi,
            weighted_roi_delta=transition.weighted_roi_delta,
            realized_weighted_roi_after=transition.state.realized_weighted_roi,created_at=now)
        s.add(ledger);await s.flush()
        return transition.state.realized_weighted_roi

    async def tp_action_availability(self,candidate_id:int,signalist_id:int):
        async with self.sessions() as s:
            candidate=await s.get(SignalUpdateCandidateRecord,candidate_id)
            event=await s.get(SignalEventRecord,candidate.event_id) if candidate else None
            signal=await s.get(SignalRecord,candidate.signal_id) if candidate else None
            position=await s.get(SignalPositionStateRecord,candidate.signal_id) if candidate else None
            if not candidate or not event or event.kind is not SignalEventKind.TP_HIT or not signal or signal.signalist_id!=signalist_id or not position or position.is_closed:return ()
            count=await s.scalar(select(__import__('sqlalchemy').func.count()).select_from(SignalTakeProfitRecord).where(SignalTakeProfitRecord.signal_id==signal.id))
            result=[PositionActionKind.NORMAL_TP,PositionActionKind.FULL_CLOSE]
            if event.tp_index != count:
                if not position.risk_free_used:result.insert(1,PositionActionKind.RISK_FREE)
                if not position.save_profit_50_used:result.insert(-1,PositionActionKind.SAVE_PROFIT_50)
            return tuple(result)

    async def decide_tp_action(self,candidate_id:int,signalist_id:int,action:PositionActionKind):
        """Atomically decide one TP candidate and mutate authoritative accounting."""
        now=self.clock();wake=False
        try:
            async with self.sessions() as s,s.begin():
                candidate=await s.get(SignalUpdateCandidateRecord,candidate_id)
                event=await s.get(SignalEventRecord,candidate.event_id) if candidate else None
                signal=await s.get(SignalRecord,candidate.signal_id) if candidate else None
                actor=await s.get(SignalistRecord,signalist_id)
                existing=await s.scalar(select(SignalPositionActionRecord).where(
                    SignalPositionActionRecord.candidate_id==candidate_id))
                if existing is not None:
                    if (existing.action_kind is action or
                        (action is PositionActionKind.NORMAL_TP and
                         existing.action_kind is PositionActionKind.FINAL_TP_CLOSE)):
                        return existing.action_kind
                    raise ValueError("candidate is already decided")
                if (not candidate or candidate.status is not CandidateStatus.AWAITING_DECISION or
                    candidate.superseded_by_event_id is not None or not event or
                    event.kind is not SignalEventKind.TP_HIT or not signal or
                    signal.status is not SignalLifecycleStatus.ACTIVE or
                    signal.signalist_id!=signalist_id or not actor or
                    actor.status is not SignalistStatus.ACTIVE):raise ValueError("candidate is not actionable")
                position=await s.get(SignalPositionStateRecord,signal.id)
                monitor=await s.get(SignalMonitorStateRecord,signal.id)
                if not position or position.is_closed or not monitor:raise ValueError("position is not actionable")
                tp_count=await s.scalar(select(__import__('sqlalchemy').func.count()).select_from(
                    SignalTakeProfitRecord).where(SignalTakeProfitRecord.signal_id==signal.id))
                final=event.tp_index==tp_count
                if action is PositionActionKind.NORMAL_TP:ledger_action=PositionActionKind.FINAL_TP_CLOSE if final else action
                elif action in (PositionActionKind.RISK_FREE,PositionActionKind.SAVE_PROFIT_50):
                    if final:raise ValueError("action is unavailable for final TP")
                    ledger_action=action
                elif action is PositionActionKind.FULL_CLOSE:ledger_action=action
                else:raise ValueError("unsupported TP action")
                final_roi=await self._apply_position(s,position,signal,event,candidate.id,ledger_action,now)
                candidate.status=CandidateStatus.APPROVED;candidate.approved_at=now;candidate.updated_at=now
                s.add(UpdatePublicationRequestRecord(candidate_id=candidate.id,signal_id=signal.id,
                    event_id=event.id,status=PublicationRequestStatus.PENDING,created_at=now,updated_at=now))
                if ledger_action in (PositionActionKind.FINAL_TP_CLOSE,PositionActionKind.FULL_CLOSE):
                    monitor.stage=(MonitorStage.COMPLETED_TARGETS if ledger_action is PositionActionKind.FINAL_TP_CLOSE
                                   else MonitorStage.COMPLETED_FULL_CLOSE)
                    monitor.current_result_kind=self._result_kind(final_roi)
                    monitor.current_leveraged_roi=final_roi;monitor.current_result_price=event.roi_basis_price
                    monitor.completed_at=now;monitor.updated_at=now;monitor.version+=1
                    await s.execute(delete(DestinationTradeLockRecord).where(DestinationTradeLockRecord.signal_id==signal.id))
                wake=True
        except IntegrityError as exc:
            raise ValueError("candidate is already decided") from exc
        if wake and self.update_publication_wake:self.update_publication_wake()
        return ledger_action

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
        # No market call is needed to close an already-confirmed final target whose
        # owner can no longer choose presentation semantics.
        await self.reconcile_inactive_final_positions()
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
            event=await s.get(SignalEventRecord,row.event_id) if row else None
            if row is None or signal is None or signal.status is not SignalLifecycleStatus.ACTIVE or row.signalist_id!=signalist_id or actor is None or actor.status is not SignalistStatus.ACTIVE:raise ValueError("candidate is not actionable")
            legacy_terminal=(event is not None and event.kind is SignalEventKind.TP_HIT and
                row.superseded_by_event_id is None and
                await self._is_legacy_terminal_tp(s,row,event,signal))
            # Generic TP decisions remain forbidden except for the narrowly
            # proven pre-4B terminal publication decision shape.
            if event is None or (event.kind is SignalEventKind.TP_HIT and not legacy_terminal):
                raise ValueError("TP candidates require a position action")
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
