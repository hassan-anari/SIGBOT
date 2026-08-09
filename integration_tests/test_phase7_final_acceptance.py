"""Consolidated Phase 7 lifecycle, reporting, and restart acceptance coverage.

Focused Phase 7.1--7.4 modules retain the detailed creation, exact-price,
template, publication-chain, active-browser, and cancellation scenarios.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chainura.application.monitoring import MonitoringService
from chainura.application.publication import CancellationPublicationService
from chainura.application.reporting import ReportingRepository, period_filter
from chainura.domain.destination import Exchange
from chainura.domain.monitoring import MonitorStage, ResultKind
from chainura.domain.publication import PublicationStatus
from chainura.domain.signal import PriceSourceType, SignalLifecycleStatus, SignalMode, TradeDirection
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.base import Base
from chainura.persistence.models import (
    DestinationPublicationRecord, SignalCancellationPublicationRecord,
    SignalEventRecord, SignalExchangeSymbolRecord, SignalInitialPriceRecord,
    SignalMonitorStateRecord, SignalRecord, SignalistRecord,
)
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory

NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


class Phase7FinalAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.transactions = SqlAlchemySignalistTransactionFactory(self.sessions)
        async with self.sessions() as session, session.begin():
            actor = SignalistRecord(telegram_user_id=70001, display_name="Final",
                status=SignalistStatus.ACTIVE, created_at=NOW, updated_at=NOW)
            session.add(actor); await session.flush(); self.actor_id = actor.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _signal(self, token, lifecycle, stage=None, result=None, roi=None, highest=0):
        async with self.sessions() as session, session.begin():
            signal = SignalRecord(signalist_id=self.actor_id, creation_token=token,
                mode=SignalMode.COMPOSITE, canonical_symbol=f"{token.upper()}USDT",
                base_asset=token.upper(), quote_asset="USDT", direction=TradeDirection.LONG,
                entry_price=Decimal("100"), leverage=10,
                stop_loss=None if lifecycle is SignalLifecycleStatus.PENDING_DETAILS else Decimal("90"),
                reference_price=Decimal("100"), price_source_type=PriceSourceType.COMPOSITE,
                status=lifecycle, cancelled_at=NOW if lifecycle is SignalLifecycleStatus.CANCELLED else None,
                created_at=NOW, updated_at=NOW)
            session.add(signal); await session.flush()
            if stage is not None:
                session.add(SignalMonitorStateRecord(signal_id=signal.id, stage=stage,
                    highest_tp_index=highest, current_result_kind=result,
                    current_result_price=Decimal("110") if result else None,
                    current_leveraged_roi=roi,
                    completed_at=NOW if stage in (MonitorStage.COMPLETED_TARGETS,
                        MonitorStage.COMPLETED_SL, MonitorStage.COMPLETED_BREAKEVEN) else None,
                    created_at=NOW, updated_at=NOW))
            return signal.id

    async def test_final_reporting_categories_denominators_and_history(self):
        await self._signal("pending", SignalLifecycleStatus.PENDING_DETAILS)
        await self._signal("open", SignalLifecycleStatus.ACTIVE, MonitorStage.PRE_TP1)
        await self._signal("win", SignalLifecycleStatus.ACTIVE, MonitorStage.COMPLETED_TARGETS,
            ResultKind.WIN, Decimal("100"), 1)
        await self._signal("loss", SignalLifecycleStatus.ACTIVE, MonitorStage.COMPLETED_SL,
            ResultKind.LOSS, Decimal("-100"))
        await self._signal("be", SignalLifecycleStatus.ACTIVE, MonitorStage.COMPLETED_BREAKEVEN,
            ResultKind.NEUTRAL, Decimal("0"))
        await self._signal("cancel", SignalLifecycleStatus.CANCELLED, MonitorStage.PRE_TP1)
        await self._signal("cancelwin", SignalLifecycleStatus.CANCELLED, MonitorStage.POST_TP1,
            ResultKind.WIN, Decimal("100"), 1)

        repo = ReportingRepository(self.sessions); report_filter = period_filter("today", NOW)
        summary = await repo.summary(report_filter)
        self.assertEqual((summary.total_signals, summary.pending_signals, summary.open_signals,
            summary.cancelled_signals, summary.resulted_signals), (7, 1, 1, 2, 3))
        self.assertEqual((summary.wins, summary.losses, summary.breakevens), (1, 1, 1))
        self.assertEqual(summary.win_rate, Decimal("50"))
        self.assertEqual(summary.total_roi, Decimal("0"))
        self.assertEqual((summary.tp1_count, summary.active_signals), (1, 1))
        history = {item.canonical_symbol: item for item in (await repo.history(report_filter)).items}
        self.assertEqual(history["PENDINGUSDT"].state, "Pending Details")
        self.assertEqual(history["CANCELUSDT"].state, "Cancelled")
        self.assertEqual(history["CANCELWINUSDT"].state, "Cancelled after TP1")
        self.assertEqual(history["CANCELWINUSDT"].highest_tp, 1)
        self.assertIsNone(history["CANCELWINUSDT"].theoretical_roi)

    async def test_restart_recovery_keeps_cancelled_terminal_and_outbox_safe(self):
        signal_id = await self._signal("terminal", SignalLifecycleStatus.CANCELLED)
        async with self.sessions() as session, session.begin():
            row = SignalCancellationPublicationRecord(signal_id=signal_id, destination_id=77,
                status=PublicationStatus.SENDING, created_at=NOW, updated_at=NOW)
            session.add(row); await session.flush(); row_id = row.id
        service = CancellationPublicationService(self.transactions, publisher=None, clock=lambda: NOW)
        await service.recover()
        await MonitoringService(self.sessions, clock=lambda: NOW).reconcile()
        async with self.sessions() as session:
            row = await session.get(SignalCancellationPublicationRecord, row_id)
            self.assertIs(row.status, PublicationStatus.DELIVERY_UNKNOWN)
            self.assertEqual(row.last_error_category, "restart_during_send")
            self.assertIsNone(await session.get(SignalMonitorStateRecord, signal_id))
            self.assertEqual(await session.scalar(select(func.count()).select_from(SignalEventRecord)
                .where(SignalEventRecord.signal_id == signal_id)), 0)
