import unittest
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from sqlalchemy import func,select

from chainura.domain.destination import DestinationAccessLevel,DestinationStatus,DestinationType,Exchange
from chainura.domain.monitoring import CandidateStatus,SignalEventKind
from chainura.domain.position import PositionActionKind
from chainura.domain.publication import PublicationRequestStatus,PublicationStatus
from chainura.domain.signal import PriceSourceType,SignalLifecycleStatus,SignalMode,TradeDirection
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import *
from chainura.persistence.update_publication_repository import SqlAlchemyUpdatePublicationRepository
from integration_tests.support import create_safe_engine,migrate,require_safe_test_database

NOW=datetime(2026,9,27,tzinfo=timezone.utc)

class Phase4BOrderingIntegrationTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.safe=require_safe_test_database();migrate('head',self.safe);self.engine=create_safe_engine(self.safe);self.sessions=create_session_factory(self.engine)
  async with self.sessions() as s,s.begin():
   actor=SignalistRecord(telegram_user_id=9911,display_name='P4B',status=SignalistStatus.ACTIVE,created_at=NOW,updated_at=NOW);s.add(actor);await s.flush();self.actor=actor.id
   dest=DestinationRecord(internal_name='phase4b',telegram_chat_id=-1009911,destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PUBLIC,status=DestinationStatus.ACTIVE,daily_limit=10,created_at=NOW,updated_at=NOW);s.add(dest);await s.flush();self.dest=dest.id
   s.add(DestinationSignalistRecord(destination_id=dest.id,signalist_id=actor.id,created_at=NOW))
   signal=SignalRecord(signalist_id=actor.id,creation_token='p4b-order',mode=SignalMode.COMPOSITE,canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',direction=TradeDirection.LONG,entry_price=Decimal('100'),leverage=10,stop_loss=Decimal('90'),reference_price=Decimal('100'),price_source_type=PriceSourceType.COMPOSITE,status=SignalLifecycleStatus.ACTIVE,created_at=NOW,updated_at=NOW);s.add(signal);await s.flush();self.signal=signal.id
   initial=DestinationPublicationRecord(signal_id=signal.id,destination_id=dest.id,event_kind='initial',selected_exchange=Exchange.OURBIT,status=PublicationStatus.SENT,telegram_message_id=10,sent_at=NOW,created_at=NOW,updated_at=NOW);s.add(initial)
   event=SignalEventRecord(signal_id=signal.id,kind=SignalEventKind.TP_HIT,tp_index=1,threshold_price=Decimal('110'),observed_reference_price=Decimal('110'),roi_basis_price=Decimal('110'),leveraged_roi=Decimal('100'),price_source_type=PriceSourceType.COMPOSITE,occurred_at=NOW,created_at=NOW);s.add(event);await s.flush()
   s.add(SignalEventSourcePriceRecord(event_id=event.id,exchange=Exchange.OURBIT,price=Decimal('110')))
   candidate=SignalUpdateCandidateRecord(signal_id=signal.id,event_id=event.id,signalist_id=actor.id,status=CandidateStatus.APPROVED,approved_at=NOW,created_at=NOW,updated_at=NOW);s.add(candidate);await s.flush()
   req=UpdatePublicationRequestRecord(candidate_id=candidate.id,signal_id=signal.id,event_id=event.id,status=PublicationRequestStatus.PLANNED,created_at=NOW,updated_at=NOW);s.add(req);await s.flush()
   tp=DestinationUpdatePublicationRecord(request_id=req.id,candidate_id=candidate.id,signal_id=signal.id,event_id=event.id,destination_id=dest.id,selected_exchange=Exchange.OURBIT,event_kind=SignalEventKind.TP_HIT,status=PublicationStatus.SENDING,reply_to_message_id=10,attempt_count=1,created_at=NOW,updated_at=NOW);s.add(tp);await s.flush();self.tp=tp.id
   action=SignalPositionActionRecord(signal_id=signal.id,event_id=event.id,candidate_id=candidate.id,action_kind=PositionActionKind.RISK_FREE,remaining_fraction_before=Decimal('1'),closed_fraction=Decimal('0'),remaining_fraction_after=Decimal('1'),execution_price=Decimal('110'),leveraged_roi=Decimal('100'),weighted_roi_delta=Decimal('0'),realized_weighted_roi_after=Decimal('0'),created_at=NOW);s.add(action);await s.flush();self.action=action.id
 async def asyncTearDown(self):await self.engine.dispose();self.safe.cleanup()
 async def test_sent_transition_atomically_materializes_barrier_and_is_idempotent(self):
  async with self.sessions() as s,s.begin():
   repo=SqlAlchemyUpdatePublicationRepository(s);self.assertTrue(await repo.transition(self.tp,PublicationStatus.SENDING,PublicationStatus.SENT,NOW,message_id=20))
  async with self.sessions() as s:
   row=(await s.scalars(select(SignalPositionActionPublicationRecord))).one()
   self.assertEqual((row.action_id,row.tp_publication_id,row.reply_to_message_id,row.status),(self.action,self.tp,20,PublicationStatus.PENDING));action_pub=row.id
  async with self.sessions() as s,s.begin():
   repo=SqlAlchemyUpdatePublicationRepository(s);self.assertFalse(await repo.transition(self.tp,PublicationStatus.SENDING,PublicationStatus.SENT,NOW,message_id=20))
  async with self.sessions() as s:self.assertEqual(await s.scalar(select(func.count()).select_from(SignalPositionActionPublicationRecord)),1)
  # A later update cannot claim while the atomically-created text barrier is pending.
  async with self.sessions() as s,s.begin():
   event=SignalEventRecord(signal_id=self.signal,kind=SignalEventKind.SL_HIT,tp_index=None,threshold_price=Decimal('90'),observed_reference_price=Decimal('90'),roi_basis_price=Decimal('90'),leveraged_roi=Decimal('-100'),price_source_type=PriceSourceType.COMPOSITE,occurred_at=NOW,created_at=NOW);s.add(event);await s.flush();s.add(SignalEventSourcePriceRecord(event_id=event.id,exchange=Exchange.OURBIT,price=Decimal('90')))
   candidate=SignalUpdateCandidateRecord(signal_id=self.signal,event_id=event.id,signalist_id=self.actor,status=CandidateStatus.APPROVED,approved_at=NOW,created_at=NOW,updated_at=NOW);s.add(candidate);await s.flush();req=UpdatePublicationRequestRecord(candidate_id=candidate.id,signal_id=self.signal,event_id=event.id,status=PublicationRequestStatus.PLANNED,created_at=NOW,updated_at=NOW);s.add(req);await s.flush();later=DestinationUpdatePublicationRecord(request_id=req.id,candidate_id=candidate.id,signal_id=self.signal,event_id=event.id,destination_id=self.dest,selected_exchange=Exchange.OURBIT,event_kind=SignalEventKind.SL_HIT,status=PublicationStatus.PENDING,created_at=NOW,updated_at=NOW);s.add(later);await s.flush();later_id=later.id
  async with self.sessions() as s,s.begin():self.assertIsNone(await SqlAlchemyUpdatePublicationRepository(s).claim(later_id,NOW))
  async with self.sessions() as s,s.begin():
   action_row=await s.get(SignalPositionActionPublicationRecord,action_pub);action_row.status=PublicationStatus.SENT;action_row.telegram_message_id=30;action_row.sent_at=NOW+timedelta(seconds=1)
  async with self.sessions() as s,s.begin():self.assertIsNotNone(await SqlAlchemyUpdatePublicationRepository(s).claim(later_id,NOW))
  async with self.sessions() as s:self.assertEqual(await SqlAlchemyUpdatePublicationRepository(s).last_published_message_id(self.signal,self.dest),30)

 async def test_non_sent_tp_never_materializes_action_text(self):
  for status in (PublicationStatus.DELIVERY_UNKNOWN,PublicationStatus.FAILED,PublicationStatus.SUPERSEDED):
   async with self.sessions() as s,s.begin():
    tp=await s.get(DestinationUpdatePublicationRecord,self.tp);tp.status=PublicationStatus.SENDING
    self.assertTrue(await SqlAlchemyUpdatePublicationRepository(s).transition(self.tp,PublicationStatus.SENDING,status,NOW,error='test'))
   async with self.sessions() as s:self.assertEqual(await s.scalar(select(func.count()).select_from(SignalPositionActionPublicationRecord)),0)

if __name__=='__main__':unittest.main()
