import asyncio
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, text

from chainura.application.monitoring import MonitoringService
from chainura.application.position_accounting import (ApplyPositionAction,
    PositionAccountingService, StalePositionDecision)
from chainura.application.signal_service import CreateSignalCommand, ReferencePriceResult, SignalService
from chainura.domain.destination import Exchange
from chainura.domain.monitoring import CandidateStatus, MonitorStage, SignalEventKind
from chainura.domain.position import PositionActionKind
from chainura.domain.signal import PriceSourceType, SignalLifecycleStatus, SignalMode, TradeDirection
from chainura.domain.signalist import Signalist
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import (SignalEventRecord, SignalMonitorStateRecord,
    SignalPositionActionRecord, SignalPositionStateRecord, SignalRecord,
    SignalUpdateCandidateRecord)
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from integration_tests.support import create_safe_engine, migrate, require_safe_test_database

NOW=datetime(2026,8,15,tzinfo=timezone.utc)

class Prices:
    async def get_reference_price(self,**kwargs):
        return ReferencePriceResult(Decimal('100'),PriceSourceType.COMPOSITE,
            ((Exchange.OURBIT,Decimal('100')),(Exchange.TOOBIT,Decimal('100'))),True,
            'BTCUSDT',tuple((exchange,'BTCUSDT') for exchange in Exchange))

class PositionAccountingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.safe=require_safe_test_database();migrate('head',self.safe)
        self.engine=create_safe_engine(self.safe);self.sessions=create_session_factory(self.engine)
        async with self.sessions() as session,session.begin():
            self.actor=await SqlAlchemySignalistRepository(session).add(Signalist.create(880015,'Position'))
        self.signal=await SignalService(SqlAlchemySignalistTransactionFactory(self.sessions),Prices(),lambda:NOW).create(
            CreateSignalCommand(self.actor.id,'position-token',SignalMode.COMPOSITE,'BTCUSDT','BTC','USDT',None,None,
                TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('90')))
    async def asyncTearDown(self):
        await self.engine.dispose();self.safe.cleanup()

    async def test_compatibility_creation_and_duplicate_candidate_are_atomic(self):
        async with self.sessions() as session,session.begin():
            state=await session.get(SignalPositionStateRecord,self.signal.id)
            self.assertEqual((state.remaining_fraction,state.realized_weighted_roi,state.effective_stop_price,state.version),(Decimal('1'),Decimal('0'),Decimal('90'),1))
            event=SignalEventRecord(signal_id=self.signal.id,kind=SignalEventKind.TP_HIT,tp_index=1,
                threshold_price=Decimal('110'),observed_reference_price=Decimal('110'),roi_basis_price=Decimal('110'),
                leveraged_roi=Decimal('20'),price_source_type=PriceSourceType.COMPOSITE,occurred_at=NOW,created_at=NOW)
            session.add(event);await session.flush()
            candidate=SignalUpdateCandidateRecord(signal_id=self.signal.id,event_id=event.id,signalist_id=self.actor.id,
                status=CandidateStatus.AWAITING_DECISION,created_at=NOW,updated_at=NOW)
            session.add(candidate);await session.flush();event_id=event.id;candidate_id=candidate.id
        command=ApplyPositionAction(self.signal.id,event_id,candidate_id,1,
            PositionActionKind.SAVE_PROFIT_50,NOW)
        results=await asyncio.gather(PositionAccountingService(self.sessions).apply(command),
            PositionAccountingService(self.sessions).apply(command),return_exceptions=True)
        self.assertEqual(sum(result is True for result in results),1)
        self.assertTrue(any(result is False or isinstance(result,StalePositionDecision)
                            for result in results))
        async with self.sessions() as session:
            state=await session.get(SignalPositionStateRecord,self.signal.id)
            self.assertEqual((state.remaining_fraction,state.realized_weighted_roi,state.version),(Decimal('.5'),Decimal('10'),2))
            actions=(await session.scalars(select(SignalPositionActionRecord))).all()
            self.assertEqual(len(actions),1)
            self.assertEqual((actions[0].execution_price,actions[0].leveraged_roi),
                (Decimal('110'),Decimal('20')))

    async def test_risk_free_uses_persisted_signal_entry(self):
        async with self.sessions() as session,session.begin():
            event=SignalEventRecord(signal_id=self.signal.id,kind=SignalEventKind.TP_HIT,tp_index=1,
                threshold_price=Decimal('111'),observed_reference_price=Decimal('112'),
                roi_basis_price=Decimal('111'),leveraged_roi=Decimal('37.125'),
                price_source_type=PriceSourceType.COMPOSITE,occurred_at=NOW,created_at=NOW)
            session.add(event);await session.flush();event_id=event.id
        # The command intentionally has no caller-supplied price, ROI, or stop.
        applied=await PositionAccountingService(self.sessions).apply(ApplyPositionAction(
            self.signal.id,event_id,None,1,PositionActionKind.RISK_FREE,NOW))
        self.assertTrue(applied)
        async with self.sessions() as session:
            state=await session.get(SignalPositionStateRecord,self.signal.id)
            action=(await session.scalars(select(SignalPositionActionRecord))).one()
            self.assertEqual(state.effective_stop_price,Decimal('100'))
            self.assertEqual((action.execution_price,action.leveraged_roi,
                action.weighted_roi_delta),(Decimal('111'),Decimal('37.125'),Decimal('0')))

    async def test_reconcile_only_open_active_signals_idempotently(self):
        async with self.sessions() as session,session.begin():
            await session.execute(text('DELETE FROM signal_position_states WHERE signal_id=:s'),{'s':self.signal.id})
            terminal=SignalRecord(signalist_id=self.actor.id,creation_token='terminal',mode=SignalMode.COMPOSITE,
                canonical_symbol='ETHUSDT',base_asset='ETH',quote_asset='USDT',direction=TradeDirection.LONG,
                entry_price=Decimal('100'),leverage=10,stop_loss=Decimal('90'),reference_price=Decimal('100'),
                price_source_type=PriceSourceType.COMPOSITE,status=SignalLifecycleStatus.ACTIVE,created_at=NOW,updated_at=NOW)
            session.add(terminal);await session.flush()
            session.add(SignalMonitorStateRecord(signal_id=terminal.id,stage=MonitorStage.COMPLETED_TARGETS,
                created_at=NOW,updated_at=NOW,completed_at=NOW));terminal_id=terminal.id
        service=MonitoringService(self.sessions,clock=lambda:NOW)
        await service.reconcile();await service.reconcile()
        async with self.sessions() as session:
            states=(await session.scalars(select(SignalPositionStateRecord))).all()
            self.assertEqual([row.signal_id for row in states],[self.signal.id])
            self.assertEqual(states[0].effective_stop_price,Decimal('90'))
            self.assertIsNone(await session.get(SignalPositionStateRecord,terminal_id))
