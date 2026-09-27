import unittest
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, update

from chainura.application.active_signals import ActiveSignalService
from chainura.application.monitoring import CURRENT_INTERACTION_VERSION, MonitoringService
from chainura.application.pagination import PageRequest
from chainura.application.signal_service import CreateSignalCommand, ReferencePriceResult, SignalService
from chainura.domain.destination import DestinationAccessLevel, DestinationStatus, DestinationType, Exchange
from chainura.domain.monitoring import (CandidateStatus, MonitorStage, NotificationKind,
    NotificationStatus, SignalEventKind)
from chainura.domain.position import PositionActionKind
from chainura.domain.signal import PriceSourceType, SignalLifecycleStatus, SignalMode, TradeDirection
from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.domain.publication import PublicationStatus
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import (DestinationRecord, DestinationTradeLockRecord,
    DestinationUpdatePublicationRecord, SignalEventRecord, SignalMonitorStateRecord,
    SignalPositionActionRecord, SignalPositionStateRecord, SignalRecord,
    SignalUpdateCandidateRecord, SignalistNotificationRecord, SignalistRecord,
    UpdatePublicationRequestRecord)
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.telegram.active_signal_routes import detail_keyboard, detail_text
from chainura.telegram.presentation import texts
from integration_tests.support import create_safe_engine, migrate, require_safe_test_database

NOW=datetime(2026,8,17,tzinfo=timezone.utc)


class Prices:
    async def get_reference_price(self,**kwargs):
        return ReferencePriceResult(Decimal("100"),PriceSourceType.COMPOSITE,
            ((Exchange.OURBIT,Decimal("100")),(Exchange.TOOBIT,Decimal("100"))),True,
            "BTCUSDT",tuple((exchange,"BTCUSDT") for exchange in Exchange))


class ProductionUpgradeCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.safe=require_safe_test_database();migrate("head",self.safe)
        self.engine=create_safe_engine(self.safe);self.sessions=create_session_factory(self.engine)
        async with self.sessions() as s,s.begin():
            self.actor=await SqlAlchemySignalistRepository(s).add(Signalist.create(817001,"Upgrade"))
        self.tx=SqlAlchemySignalistTransactionFactory(self.sessions)
        self.signal=await SignalService(self.tx,Prices(),lambda:NOW).create(CreateSignalCommand(
            self.actor.id,"upgrade-main",SignalMode.COMPOSITE,"BTCUSDT","BTC","USDT",None,None,
            TradeDirection.LONG,Decimal("100"),10,(Decimal("110"),Decimal("120")),Decimal("90")))

    async def asyncTearDown(self):
        await self.engine.dispose();self.safe.cleanup()

    async def _candidate(self,status=CandidateStatus.AWAITING_NOTIFICATION,kind=SignalEventKind.TP_HIT,
                         tp_index=1,new_signal=False):
        signal=self.signal
        if new_signal:
            signal=await SignalService(self.tx,Prices(),lambda:NOW).create(CreateSignalCommand(
                self.actor.id,f"upgrade-{id(status)}-{kind.value}-{len(getattr(self,'_tokens',()))}",
                SignalMode.COMPOSITE,"BTCUSDT","BTC","USDT",None,None,TradeDirection.LONG,
                Decimal("100"),10,(Decimal("110"),Decimal("120")),Decimal("90")))
            self._tokens=(*getattr(self,"_tokens",()),signal.id)
        async with self.sessions() as s,s.begin():
            event=SignalEventRecord(signal_id=signal.id,kind=kind,tp_index=tp_index,
                threshold_price=Decimal("110"),observed_reference_price=Decimal("111"),
                roi_basis_price=Decimal("110"),leveraged_roi=Decimal("100"),
                price_source_type=PriceSourceType.COMPOSITE,occurred_at=NOW,created_at=NOW)
            s.add(event);await s.flush()
            candidate=SignalUpdateCandidateRecord(signal_id=signal.id,event_id=event.id,
                signalist_id=self.actor.id,status=status,created_at=NOW,updated_at=NOW)
            s.add(candidate);await s.flush();return candidate.id

    async def test_all_legacy_tp_delivery_states_are_refreshed_once(self):
        states=(NotificationStatus.PENDING,NotificationStatus.RETRY_WAIT,NotificationStatus.SENT,
                NotificationStatus.DELIVERY_UNKNOWN,NotificationStatus.FAILED)
        candidate_ids=[]
        for state in states:
            candidate_ids.append(await self._candidate(CandidateStatus.AWAITING_DECISION,new_signal=True))
            async with self.sessions() as s,s.begin():
                s.add(SignalistNotificationRecord(kind=NotificationKind.EVENT,
                    candidate_id=candidate_ids[-1],signalist_id=self.actor.id,status=state,
                    interaction_version=1,attempt_count=3,telegram_message_id=55 if state is NotificationStatus.SENT else None,
                    sent_at=NOW if state is NotificationStatus.SENT else None,created_at=NOW,updated_at=NOW))
        missing=await self._candidate(new_signal=True)
        service=MonitoringService(self.sessions,clock=lambda:NOW)
        first=await service.reconcile();second=await service.reconcile()
        self.assertEqual((first.legacy_tp_interactions_refreshed,
                          first.missing_tp_notification_intents_repaired),(5,1))
        self.assertEqual((second.legacy_tp_interactions_refreshed,
                          second.missing_tp_notification_intents_repaired),(0,0))
        async with self.sessions() as s:
            notes=(await s.scalars(select(SignalistNotificationRecord).where(
                SignalistNotificationRecord.candidate_id.in_((*candidate_ids,missing))))).all()
            self.assertEqual(len(notes),6)
            self.assertTrue(all(n.interaction_version==CURRENT_INTERACTION_VERSION and
                                n.status is NotificationStatus.PENDING for n in notes))
        # The generic legacy callback remains rejected; the Phase-4 action path works.
        with self.assertRaises(ValueError):await service.decide_candidate(candidate_ids[0],self.actor.id,True)
        self.assertIs(await service.decide_tp_action(candidate_ids[0],self.actor.id,
            PositionActionKind.NORMAL_TP),PositionActionKind.NORMAL_TP)

    async def test_non_tp_and_historical_candidates_are_not_reissued(self):
        historical=await self._candidate(CandidateStatus.APPROVED)
        sl=await self._candidate(CandidateStatus.AWAITING_DECISION,SignalEventKind.SL_HIT,None)
        async with self.sessions() as s,s.begin():
            for candidate in (historical,sl):
                s.add(SignalistNotificationRecord(kind=NotificationKind.EVENT,candidate_id=candidate,
                    signalist_id=self.actor.id,status=NotificationStatus.SENT,interaction_version=1,
                    telegram_message_id=77,sent_at=NOW,created_at=NOW,updated_at=NOW))
        await MonitoringService(self.sessions,clock=lambda:NOW).reconcile()
        async with self.sessions() as s:
            rows=(await s.scalars(select(SignalistNotificationRecord).where(
                SignalistNotificationRecord.candidate_id.in_((historical,sl))))).all()
            self.assertTrue(all(x.interaction_version==1 and x.status is NotificationStatus.SENT for x in rows))

    async def test_inactive_final_target_is_terminalized_without_publication_and_is_idempotent(self):
        candidate=await self._candidate(CandidateStatus.AWAITING_DECISION,tp_index=2)
        async with self.sessions() as s,s.begin():
            monitor=await s.get(SignalMonitorStateRecord,self.signal.id)
            monitor.stage=MonitorStage.AWAITING_FINAL_TP_ACTION;monitor.highest_tp_index=2
            actor=await s.get(SignalistRecord,self.actor.id);actor.status=SignalistStatus.INACTIVE
            destination=DestinationRecord(internal_name="private",telegram_chat_id=-817001,
                destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PRIVATE,
                status=DestinationStatus.ACTIVE,daily_limit=5,created_at=NOW,updated_at=NOW)
            s.add(destination);await s.flush()
            s.add(DestinationTradeLockRecord(destination_id=destination.id,canonical_symbol="BTCUSDT",
                signal_id=self.signal.id,acquired_at=NOW,updated_at=NOW))
        service=MonitoringService(self.sessions,clock=lambda:NOW)
        first=await service.reconcile();second=await service.reconcile();await service.run_cycle()
        self.assertEqual(first.inactive_owner_final_positions_terminalized,1)
        self.assertEqual(second.inactive_owner_final_positions_terminalized,0)
        async with self.sessions() as s:
            position=await s.get(SignalPositionStateRecord,self.signal.id)
            monitor=await s.get(SignalMonitorStateRecord,self.signal.id)
            action=(await s.scalars(select(SignalPositionActionRecord))).one()
            self.assertEqual((position.remaining_fraction,position.realized_weighted_roi),(Decimal("0"),Decimal("100")))
            self.assertTrue(position.is_closed);self.assertIs(monitor.stage,MonitorStage.COMPLETED_TARGETS)
            self.assertIs(action.action_kind,PositionActionKind.FINAL_TP_CLOSE)
            self.assertEqual(await s.scalar(select(func.count()).select_from(DestinationTradeLockRecord)),0)
            self.assertEqual(await s.scalar(select(func.count()).select_from(UpdatePublicationRequestRecord)),0)
            self.assertEqual(await s.scalar(select(func.count()).select_from(DestinationUpdatePublicationRecord)),0)
            self.assertIs((await s.get(SignalUpdateCandidateRecord,candidate)).status,
                          CandidateStatus.INELIGIBLE_INACTIVE)

    async def test_disabled_legacy_is_visible_and_cancellable_without_synthetic_accounting(self):
        async with self.sessions() as s,s.begin():
            row=SignalRecord(signalist_id=self.actor.id,creation_token="unmonitorable",mode=SignalMode.COMPOSITE,
                canonical_symbol="OLDUSDT",base_asset="OLD",quote_asset="USDT",direction=TradeDirection.LONG,
                entry_price=Decimal("5"),leverage=2,stop_loss=Decimal("4"),reference_price=Decimal("5"),
                price_source_type=PriceSourceType.COMPOSITE,status=SignalLifecycleStatus.ACTIVE,
                created_at=NOW,updated_at=NOW)
            s.add(row);await s.flush();legacy_id=row.id
        summary=await MonitoringService(self.sessions,clock=lambda:NOW).reconcile()
        self.assertEqual(summary.disabled_legacy_active_signals,1)
        service=ActiveSignalService(self.tx,lambda:NOW)
        page=await service.list_open(self.actor.id,PageRequest(1));legacy=next(x for x in page.items if x.id==legacy_id)
        self.assertIs(legacy.monitor_stage,MonitorStage.DISABLED_LEGACY)
        self.assertIn(texts.ACTIVE_SIGNAL_LEGACY_UNAVAILABLE,detail_text(legacy))
        labels=[button.text for line in detail_keyboard(legacy,1).inline_keyboard for button in line]
        self.assertIn(texts.ACTIVE_SIGNAL_CANCEL,labels);self.assertNotIn(texts.ACTIVE_SIGNAL_COMMENT,labels)
        self.assertNotIn(texts.ACTIVE_SIGNAL_CONTINUE,labels)
        self.assertTrue((await service.cancel_signal(legacy_id,self.actor.id)).cancelled)
        async with self.sessions() as s:
            self.assertIs((await s.get(SignalRecord,legacy_id)).status,SignalLifecycleStatus.CANCELLED)
            self.assertIsNone(await s.get(SignalPositionStateRecord,legacy_id))
            self.assertIsNone(await s.scalar(select(SignalPositionActionRecord.id).where(
                SignalPositionActionRecord.signal_id==legacy_id)))


class NotificationInteractionMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_patch_notification_is_durably_legacy_and_new_rows_are_current(self):
        safe=require_safe_test_database()
        try:
            migrate("20260816_01",safe);engine=create_safe_engine(safe)
            async with engine.begin() as c:
                actor=(await c.exec_driver_sql("INSERT INTO signalists(telegram_user_id,display_name,status,created_at,updated_at) VALUES(817100,'legacy','active',?,?) RETURNING id",(NOW,NOW))).scalar_one()
                signal=(await c.exec_driver_sql("INSERT INTO signals(signalist_id,creation_token,mode,canonical_symbol,base_asset,quote_asset,direction,entry_price,leverage,stop_loss,reference_price,price_source_type,status,created_at,updated_at) VALUES(?,'legacy-ui','composite','BTCUSDT','BTC','USDT','long','100',10,'90','100','composite','active',?,?) RETURNING id",(actor,NOW,NOW))).scalar_one()
                event=(await c.exec_driver_sql("INSERT INTO signal_events(signal_id,kind,tp_index,threshold_price,observed_reference_price,roi_basis_price,leveraged_roi,price_source_type,occurred_at,created_at) VALUES(?,'tp_hit',1,'110','110','110','100','composite',?,?) RETURNING id",(signal,NOW,NOW))).scalar_one()
                candidate=(await c.exec_driver_sql("INSERT INTO signal_update_candidates(signal_id,event_id,signalist_id,status,created_at,updated_at) VALUES(?,?,?,'awaiting_decision',?,?) RETURNING id",(signal,event,actor,NOW,NOW))).scalar_one()
                await c.exec_driver_sql("INSERT INTO signalist_notifications(kind,candidate_id,signalist_id,status,attempt_count,telegram_message_id,sent_at,created_at,updated_at) VALUES('event',?,?,'sent',1,817,?,?,?)",(candidate,actor,NOW,NOW,NOW))
            await engine.dispose();migrate("head",safe);engine=create_safe_engine(safe)
            sessions=create_session_factory(engine)
            async with sessions() as s,s.begin():
                legacy=(await s.scalars(select(SignalistNotificationRecord))).one()
                self.assertEqual(legacy.interaction_version,1)
                second_event=SignalEventRecord(signal_id=signal,kind=SignalEventKind.SL_HIT,
                    threshold_price=Decimal("90"),observed_reference_price=Decimal("90"),
                    roi_basis_price=Decimal("90"),leveraged_roi=Decimal("-100"),
                    price_source_type=PriceSourceType.COMPOSITE,occurred_at=NOW,created_at=NOW)
                s.add(second_event);await s.flush()
                second_candidate=SignalUpdateCandidateRecord(signal_id=signal,event_id=second_event.id,
                    signalist_id=actor,status=CandidateStatus.AWAITING_NOTIFICATION,created_at=NOW,updated_at=NOW)
                s.add(second_candidate);await s.flush()
                current=SignalistNotificationRecord(kind=NotificationKind.EVENT,
                    candidate_id=second_candidate.id,signalist_id=actor,status=NotificationStatus.PENDING,
                    created_at=NOW,updated_at=NOW);s.add(current);await s.flush()
                self.assertEqual(current.interaction_version,CURRENT_INTERACTION_VERSION)
            await engine.dispose()
        finally:safe.cleanup()
