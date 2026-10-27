"""Atomic persistence boundary for future Phase 4B position decisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from chainura.domain.position import (PositionActionKind, PositionState,
    apply_position_action)
from chainura.persistence.models import (SignalEventRecord,
    SignalPositionActionRecord, SignalPositionStateRecord,
    SignalRecord, SignalUpdateCandidateRecord)


class StalePositionDecision(RuntimeError):
    """The decision was already applied or its expected version is stale."""


@dataclass(frozen=True, slots=True)
class ApplyPositionAction:
    signal_id: int
    event_id: int
    candidate_id: int | None
    expected_version: int
    action_kind: PositionActionKind
    occurred_at: datetime


class PositionAccountingService:
    def __init__(self, sessions) -> None:
        self._sessions = sessions

    async def apply(self, command: ApplyPositionAction) -> bool:
        """Apply once; return False when the durable decision key already exists."""
        async with self._sessions() as session:
            try:
                async with session.begin():
                    duplicate = await session.scalar(select(SignalPositionActionRecord.id).where(
                        (SignalPositionActionRecord.candidate_id == command.candidate_id)
                        if command.candidate_id is not None else
                        ((SignalPositionActionRecord.event_id == command.event_id) &
                         (SignalPositionActionRecord.action_kind == command.action_kind))))
                    if duplicate is not None:
                        return False
                    event = await session.get(SignalEventRecord, command.event_id)
                    signal = await session.get(SignalRecord, command.signal_id)
                    candidate = (await session.get(SignalUpdateCandidateRecord, command.candidate_id)
                                 if command.candidate_id is not None else None)
                    if signal is None or event is None or event.signal_id != command.signal_id or (
                            candidate is not None and (candidate.signal_id != command.signal_id
                                                       or candidate.event_id != command.event_id)):
                        raise ValueError("event/candidate does not belong to the signal")
                    row = await session.get(SignalPositionStateRecord, command.signal_id)
                    if row is None or row.version != command.expected_version:
                        raise StalePositionDecision("position version is stale")
                    current = PositionState(row.remaining_fraction,row.realized_weighted_roi,
                        row.risk_free_used,row.risk_free_active,row.save_profit_50_used,
                        row.effective_stop_price,row.is_closed,row.closed_at)
                    transition = apply_position_action(current,command.action_kind,
                        leveraged_roi=event.leveraged_roi,entry_price=signal.entry_price,
                        occurred_at=command.occurred_at)
                    changed=(await session.execute(update(SignalPositionStateRecord).where(
                        SignalPositionStateRecord.signal_id==command.signal_id,
                        SignalPositionStateRecord.version==command.expected_version).values(
                        remaining_fraction=transition.state.remaining_fraction,
                        realized_weighted_roi=transition.state.realized_weighted_roi,
                        risk_free_used=transition.state.risk_free_used,
                        risk_free_active=transition.state.risk_free_active,
                        save_profit_50_used=transition.state.save_profit_50_used,
                        effective_stop_price=transition.state.effective_stop_price,
                        is_closed=transition.state.is_closed,closed_at=transition.state.closed_at,
                        version=command.expected_version+1,updated_at=command.occurred_at))).rowcount
                    if changed != 1:
                        raise StalePositionDecision("position version is stale")
                    session.add(SignalPositionActionRecord(signal_id=command.signal_id,
                        event_id=command.event_id,candidate_id=command.candidate_id,
                        action_kind=command.action_kind,
                        remaining_fraction_before=transition.remaining_fraction_before,
                        closed_fraction=transition.closed_fraction,
                        remaining_fraction_after=transition.state.remaining_fraction,
                        execution_price=event.roi_basis_price,
                        leveraged_roi=transition.leveraged_roi,
                        weighted_roi_delta=transition.weighted_roi_delta,
                        realized_weighted_roi_after=transition.state.realized_weighted_roi,
                        created_at=command.occurred_at))
                    await session.flush()
                return True
            except IntegrityError as exc:
                # The candidate/event uniqueness constraints are the final race-safe
                # idempotency boundary. The entire failed transaction is rolled back.
                raw = str(exc.orig)
                if "signal_position_actions" in raw:
                    return False
                raise


def ledger_matches_position(position, actions) -> bool:
    """Verify slice continuity without repairing or revaluing the operational ledger."""
    from decimal import Decimal
    remaining, realized = Decimal("1"), Decimal("0")
    for action in actions:
        if remaining <= 0 or action.remaining_fraction_before != remaining:
            return False
        if not 0 <= action.closed_fraction <= remaining:
            return False
        remaining -= action.closed_fraction
        realized += action.weighted_roi_delta
        if (action.remaining_fraction_after != remaining or
                action.weighted_roi_delta != action.closed_fraction * action.leveraged_roi or
                action.realized_weighted_roi_after != realized):
            return False
    risk_free = any(a.action_kind is PositionActionKind.RISK_FREE for a in actions)
    saved = any(a.action_kind is PositionActionKind.SAVE_PROFIT_50 for a in actions)
    return (remaining == position.remaining_fraction and
            realized == position.realized_weighted_roi and
            position.is_closed == (remaining == 0) and
            position.risk_free_used == risk_free and position.save_profit_50_used == saved and
            position.risk_free_active == (risk_free and not position.is_closed))
