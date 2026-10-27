"""Production patch regressions on isolated SQLite and fake external boundaries."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from types import SimpleNamespace
import unittest

from sqlalchemy import func, select, update
from chainura.application.active_signals import ActiveSignalService
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.application.monitoring import MonitoringService, ManualClosePriceUnavailable
from chainura.application.publication import (PositionActionPublicationService,
    DeliveryAmbiguous, DeliveryRateLimited, DeliveryRejected)
from chainura.application.reporting import ReportingRepository, ReportFilter, DestinationReportFilter
from chainura.domain.destination import DestinationAccessLevel, DestinationStatus, DestinationType, Exchange
from chainura.domain.monitoring import CandidateStatus, MarketDecision, MonitorStage, ResultKind, SignalEventKind
from chainura.domain.position import PositionActionKind
from chainura.domain.publication import PublicationRequestStatus, PublicationStatus
from chainura.domain.signal import PriceSourceType, SignalEntryType, SignalLifecycleStatus, SignalMode, TradeDirection
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.base import Base
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import *
from chainura.persistence.publication_repository import SqlAlchemyPublicationRepository
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.telegram.active_signal_routes import detail_keyboard
from chainura.telegram.event_routes import tp_action_keyboard, event_decision_keyboard
from chainura.telegram.presentation import texts
from integration_tests.support import create_safe_engine, require_safe_test_database

NOW=datetime(2026,10,26,tzinfo=timezone.utc)

class Market:
    def __init__(self, clock): self.clock=clock; self.price=D('105'); self.hook=None; self.calls=0
    async def fresh_snapshot(self, work):
        self.calls+=1
        if self.hook: await self.hook()
        return {x.signal_id:MarketDecision(self.price,((Exchange.OURBIT,self.price),(Exchange.TOOBIT,self.price)),x.price_source_type,self.clock()) for x in work} if self.price is not None else {}

class Publisher:
    def __init__(self): self.calls=[]; self.failure=None
    async def send_text_update(self, chat, text, parent):
        self.calls.append((chat,text,parent))
        if self.failure: raise self.failure
        return 900+len(self.calls)

class TargetedTradePatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.safe=require_safe_test_database(); self.engine=create_safe_engine(self.safe)
        async with self.engine.begin() as c: await c.run_sync(Base.metadata.create_all)
        self.sessions=create_session_factory(self.engine); self.tx=SqlAlchemySignalistTransactionFactory(self.sessions)
        self.now=NOW; self.market=Market(lambda:self.now)
        self.monitor=MonitoringService(self.sessions,self.market,clock=lambda:self.now)
        self.active=ActiveSignalService(self.tx,clock=lambda:self.now)
        self.reports=ReportingRepository(self.sessions)
        self.publisher=Publisher()
        self.delivery=PositionActionPublicationService(self.tx,self.publisher,DestinationMessageTemplateService(self.tx),clock=lambda:self.now)
        async with self.sessions() as s,s.begin():
            owner=SignalistRecord(telegram_user_id=26001,display_name='Owner',status=SignalistStatus.ACTIVE,created_at=NOW,updated_at=NOW)
            other=SignalistRecord(telegram_user_id=26002,display_name='Other',status=SignalistStatus.ACTIVE,created_at=NOW,updated_at=NOW)
            dest=DestinationRecord(internal_name='PRIVATE',telegram_chat_id=-10026001,destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PRIVATE,status=DestinationStatus.ACTIVE,daily_limit=20,created_at=NOW,updated_at=NOW)
            s.add_all((owner,other,dest)); await s.flush(); self.owner=owner.id; self.other=other.id; self.dest=dest.id
            s.add(DestinationSignalistRecord(destination_id=dest.id,signalist_id=owner.id,created_at=NOW))
            s.add(DestinationExchangePriorityRecord(destination_id=dest.id,position=1,exchange=Exchange.OURBIT))
    async def asyncTearDown(self): await self.engine.dispose(); self.safe.cleanup()
    async def signal(self, *, short=False, limit=False, symbol='BTCUSDT', root=True, owner=None):
        async with self.sessions() as s,s.begin():
            row=SignalRecord(signalist_id=owner or self.owner,creation_token=f'signal-{self.now}-{symbol}-{short}-{limit}',entry_type=SignalEntryType.LIMIT if limit else SignalEntryType.MARKET,
                mode=SignalMode.COMPOSITE,canonical_symbol=symbol,base_asset=symbol[:-4],quote_asset='USDT',direction=TradeDirection.SHORT if short else TradeDirection.LONG,
                entry_price=D('100'),leverage=2,stop_loss=D('110') if short else D('90'),reference_price=D('100'),price_source_type=PriceSourceType.COMPOSITE,status=SignalLifecycleStatus.ACTIVE,created_at=self.now,updated_at=self.now)
            s.add(row); await s.flush(); sid=row.id
            for i in range(1,5): s.add(SignalTakeProfitRecord(signal_id=sid,position=i,price=D(100-i*10 if short else 100+i*10)))
            for e in (Exchange.OURBIT,Exchange.TOOBIT,Exchange.LBANK): s.add(SignalExchangeSymbolRecord(signal_id=sid,exchange=e,native_symbol=symbol))
            s.add(SignalMonitorStateRecord(signal_id=sid,stage=MonitorStage.PRE_TP1,created_at=self.now,updated_at=self.now))
            s.add(SignalPositionStateRecord(signal_id=sid,remaining_fraction=D(1),realized_weighted_roi=D(0),effective_stop_price=row.stop_loss,created_at=self.now,updated_at=self.now))
            if root:
                model=LimitPendingPublicationRecord if limit else DestinationPublicationRecord
                s.add(model(signal_id=sid,destination_id=self.dest,selected_exchange=Exchange.OURBIT,status=PublicationStatus.SENT,telegram_message_id=10,sent_at=self.now,created_at=self.now,updated_at=self.now))
                s.add(DestinationTradeLockRecord(signal_id=sid,destination_id=self.dest,canonical_symbol=symbol,acquired_at=self.now,updated_at=self.now))
        self.now+=timedelta(seconds=1)
        return sid
    async def poll(self, sid, price):
        work=next(w for w in await self.monitor.load_work(100) if w.signal_id==sid)
        self.now+=timedelta(seconds=1)
        return await self.monitor.evaluate(work,MarketDecision(D(price),((Exchange.OURBIT,D(price)),(Exchange.TOOBIT,D(price))),work.price_source_type,self.now))
    async def candidate(self,sid):
        async with self.sessions() as s,s.begin():
            c=await s.scalar(select(SignalUpdateCandidateRecord).where(SignalUpdateCandidateRecord.signal_id==sid).order_by(SignalUpdateCandidateRecord.id.desc()))
            if c.status is CandidateStatus.AWAITING_NOTIFICATION:c.status=CandidateStatus.AWAITING_DECISION
            return c.id
    async def action(self,sid,kind): return await self.monitor.decide_tp_action(await self.candidate(sid),self.owner,kind)
    async def close(self,sid): return await self.monitor.manual_full_close(sid,self.owner,await self.monitor.manual_close_version(sid,self.owner))
    async def rows(self,sid):
        async with self.sessions() as s:
            return (await s.get(SignalPositionStateRecord,sid),await s.get(SignalMonitorStateRecord,sid),
                (await s.scalars(select(SignalPositionActionRecord).where(SignalPositionActionRecord.signal_id==sid).order_by(SignalPositionActionRecord.id))).all(),
                await s.scalar(select(func.count()).select_from(DestinationTradeLockRecord).where(DestinationTradeLockRecord.signal_id==sid)))
    async def test_tp_then_sl_reports_highest_target_on_all_surfaces_without_writes(self):
        for index in (0,1,2,3):
            with self.subTest(tp=index):
                sid=await self.signal(symbol=f'B{index}USDT')
                if index: await self.poll(sid,str(100+10*index))
                await self.poll(sid,'89')
                p,m,actions,locks=await self.rows(sid)
                self.assertEqual(p.realized_weighted_roi,D('-20')); self.assertEqual(locks,0)
                flt=ReportFilter(NOW,self.now+timedelta(seconds=1),self.owner)
                # Filter to this trade's creation time without changing the cohort semantics.
                async with self.sessions() as s: created=(await s.get(SignalRecord,sid)).created_at
                flt=ReportFilter(created,created+timedelta(microseconds=1),self.owner)
                expected=D(index*20) if index else D('-20')
                before=[(a.id,a.weighted_roi_delta) for a in actions]
                general=await self.reports.summary(flt); personal=await self.reports.signalist_summary(self.owner,flt)
                destination=await self.reports.destination_summary(DestinationReportFilter(self.dest,created,created+timedelta(microseconds=1)))
                for report in (general,personal,destination):
                    self.assertEqual((report.wins,report.losses,report.total_roi),(int(index>0),int(index==0),expected))
                    self.assertEqual((report.average_roi,report.best_roi,report.worst_roi),(expected,expected,expected))
                self.assertEqual((await self.reports.history(flt)).items[0].theoretical_roi,expected)
                self.assertEqual((await self.reports.signalist_history(self.owner,flt)).items[0].realized_weighted_roi,expected)
                self.assertEqual((await self.reports.destination_history(DestinationReportFilter(self.dest,created,created+timedelta(microseconds=1)))).items[0].realized_weighted_roi,expected)
                self.assertEqual([(a.id,a.weighted_roi_delta) for a in (await self.rows(sid))[2]],before)
                self.assertEqual((await self.rows(sid))[0].realized_weighted_roi,D('-20'))
    async def test_partial_save50_weights_realized_half_and_highest_tp_half(self):
        sid=await self.signal(); await self.poll(sid,'110'); await self.action(sid,PositionActionKind.SAVE_PROFIT_50)
        self.assertEqual((await self.rows(sid))[0].remaining_fraction,D('.5'))
        await self.poll(sid,'120'); await self.poll(sid,'89')
        p,_,a,_=await self.rows(sid)
        # Operational: .5*20 + .5*(-20) = 0. Reporting: .5*20 + .5*40 = 30.
        self.assertEqual(p.realized_weighted_roi,D(0)); self.assertEqual([x.closed_fraction for x in a],[D('.5'),D('.5')])
        report=await self.reports.summary(ReportFilter(NOW,self.now+timedelta(seconds=1)))
        self.assertEqual((report.wins,report.total_roi),(1,D(30)))
    async def test_normal_risk_free_and_unpublished_targets_remain_reporting_wins(self):
        for action in (None,PositionActionKind.NORMAL_TP,PositionActionKind.RISK_FREE):
            sid=await self.signal(symbol=f'R{self.now.second}USDT'); await self.poll(sid,'110')
            if action: await self.action(sid,action)
            await self.poll(sid,'100' if action is PositionActionKind.RISK_FREE else '89')
        report=await self.reports.summary(ReportFilter(NOW,self.now+timedelta(seconds=1)))
        self.assertEqual((report.wins,report.losses,report.total_roi),(3,0,D(60)))
    async def test_manual_closes_before_tp_negative_zero_positive_short_and_limit(self):
        for price,short,limit,expected in [('95',False,False,'-10'),('100',False,False,'0'),('105',False,False,'10'),('95',True,False,'10'),('105',False,True,'10')]:
            with self.subTest(price=price,short=short,limit=limit):
                sid=await self.signal(symbol=f'M{self.now.second}USDT',short=short,limit=limit); self.market.price=D(price)
                self.assertTrue(await self.close(sid)); p,m,a,locks=await self.rows(sid)
                self.assertTrue(p.is_closed); self.assertEqual((p.realized_weighted_roi,locks),(D(expected),0)); self.assertIs(m.stage,MonitorStage.COMPLETED_FULL_CLOSE)
                self.assertEqual((len(a),a[0].closed_fraction,a[0].execution_price),(1,D(1),D(price)))
                async with self.sessions() as s:
                    e=await s.get(SignalEventRecord,a[0].event_id); self.assertIs(e.kind,SignalEventKind.MANUAL_CLOSE)
                    self.assertIsNone(e.tp_index); self.assertIsNone(await s.scalar(select(SignalUpdateCandidateRecord.id).where(SignalUpdateCandidateRecord.signal_id==sid)))
                    self.assertEqual(len((await s.scalars(select(SignalEventSourcePriceRecord).where(SignalEventSourcePriceRecord.event_id==e.id))).all()),2)
                with self.assertRaises(ValueError):await self.close(sid)
                self.assertEqual((await self.reports.signalist_history(self.owner,ReportFilter(NOW,self.now+timedelta(seconds=1)))).items[0].realized_weighted_roi,D(expected))
                await self.delivery.plan()
                for pid in await self.delivery.pending_ids():await self.delivery.process(pid)
                self.assertEqual(self.publisher.calls[-1][2],10)
    async def test_manual_close_after_each_nonterminal_action_uses_remaining_volume(self):
        for kind in (PositionActionKind.NORMAL_TP,PositionActionKind.RISK_FREE,PositionActionKind.SAVE_PROFIT_50):
            sid=await self.signal(symbol=f'A{self.now.second}USDT'); await self.poll(sid,'110'); await self.action(sid,kind)
            self.market.price=D('105'); await self.close(sid); p,_,a,locks=await self.rows(sid)
            expected=D(15) if kind is PositionActionKind.SAVE_PROFIT_50 else D(10)
            self.assertEqual((p.realized_weighted_roi,locks),(expected,0))
            self.assertEqual(a[-1].closed_fraction,D('.5') if kind is PositionActionKind.SAVE_PROFIT_50 else D(1))
    async def test_price_failure_ownership_stale_and_legacy_callbacks_leave_trade_open(self):
        sid=await self.signal(); version=await self.monitor.manual_close_version(sid,self.owner)
        self.market.price=None
        with self.assertRaises(ManualClosePriceUnavailable):await self.monitor.manual_full_close(sid,self.owner,version)
        self.assertEqual((await self.rows(sid))[3],1)
        with self.assertRaises(ValueError):await self.monitor.manual_full_close(sid,self.other,version)
        await self.poll(sid,'110'); cid=await self.candidate(sid)
        with self.assertRaises(ValueError):await self.monitor.decide_tp_action(cid,self.owner,PositionActionKind.FULL_CLOSE)
        self.assertFalse((await self.rows(sid))[0].is_closed)
        await self.action(sid,PositionActionKind.SAVE_PROFIT_50); self.market.price=D(105)
        with self.assertRaises(ValueError):await self.monitor.manual_full_close(sid,self.owner,version)
        self.assertEqual((await self.rows(sid))[3],1)
    async def test_two_manual_callbacks_apply_exactly_once(self):
        sid=await self.signal(); version=await self.monitor.manual_close_version(sid,self.owner)
        result=await asyncio.gather(*(self.monitor.manual_full_close(sid,self.owner,version) for _ in range(2)),return_exceptions=True)
        self.assertEqual(sum(x is True for x in result),1); self.assertEqual(len((await self.rows(sid))[2]),1)
    async def test_manual_close_rejects_monitor_or_cancel_race_during_snapshot(self):
        for race in ('sl','tp','cancel'):
            sid=await self.signal(symbol=f'C{self.now.second}USDT'); version=await self.monitor.manual_close_version(sid,self.owner)
            async def hook():
                if race=='cancel':await self.active.cancel_signal(sid,self.owner)
                else:await self.poll(sid,'89' if race=='sl' else '110')
            self.market.hook=hook
            with self.assertRaises(ValueError):await self.monitor.manual_full_close(sid,self.owner,version)
            self.market.hook=None
            p,m,a,lock=await self.rows(sid)
            self.assertFalse(any(x.action_kind is PositionActionKind.FULL_CLOSE for x in a))
            self.assertEqual(lock,int(race=='tp'))
    async def test_final_tp_closes_without_action_intermediate_keeps_lock_and_publication_still_works(self):
        sid=await self.signal(); await self.poll(sid,'110'); self.assertEqual((await self.rows(sid))[3],1)
        await self.action(sid,PositionActionKind.SAVE_PROFIT_50); await self.poll(sid,'140')
        p,m,a,locks=await self.rows(sid)
        # Actual .5*20 + .5*80 = 50, exactly once, before publication.
        self.assertEqual((p.realized_weighted_roi,locks),(D(50),0)); self.assertTrue(p.is_closed)
        self.assertIs(m.stage,MonitorStage.COMPLETED_TARGETS); self.assertIsNone(a[-1].candidate_id)
        cid=await self.candidate(sid); self.assertEqual(await self.monitor.tp_action_availability(cid,self.owner),())
        self.assertIs(await self.monitor.decide_candidate(cid,self.owner,True),CandidateStatus.APPROVED)
        await self.monitor.decide_candidate(cid,self.owner,True)
        async with self.sessions() as s,s.begin():
            self.assertEqual(await s.scalar(select(func.count()).select_from(UpdatePublicationRequestRecord).where(UpdatePublicationRequestRecord.candidate_id==cid)),1)
            await __import__('chainura.persistence.update_publication_repository',fromlist=['SqlAlchemyUpdatePublicationRepository']).SqlAlchemyUpdatePublicationRepository(s).plan_pending(self.now)
            pubs=(await s.scalars(select(DestinationUpdatePublicationRecord).where(DestinationUpdatePublicationRecord.candidate_id==cid))).all()
            self.assertEqual(len(pubs),1)
        with self.assertRaises(ValueError):await self.monitor.decide_tp_action(cid,self.owner,PositionActionKind.NORMAL_TP)
    async def stuck(self,sid,status=CandidateStatus.AWAITING_DECISION):
        await self.poll(sid,'110'); await self.action(sid,PositionActionKind.SAVE_PROFIT_50)
        # Representative old final-TP state, retaining actual Save50 ledger.
        async with self.sessions() as s,s.begin():
            event=SignalEventRecord(signal_id=sid,kind=SignalEventKind.TP_HIT,tp_index=4,threshold_price=D(140),observed_reference_price=D(141),roi_basis_price=D(140),leveraged_roi=D(80),price_source_type=PriceSourceType.COMPOSITE,occurred_at=self.now,created_at=self.now)
            s.add(event);await s.flush()
            s.add(SignalEventSourcePriceRecord(event_id=event.id,exchange=Exchange.OURBIT,price=D(141)))
            c=SignalUpdateCandidateRecord(signal_id=sid,event_id=event.id,signalist_id=self.owner,status=status,approved_at=self.now if status is CandidateStatus.APPROVED else None,declined_at=self.now if status is CandidateStatus.DECLINED else None,created_at=self.now,updated_at=self.now)
            s.add(c);await s.flush()
            m=await s.get(SignalMonitorStateRecord,sid); m.stage=MonitorStage.AWAITING_FINAL_TP_ACTION; m.highest_tp_index=4;m.next_tp_index=5;m.current_leveraged_roi=D(80)
            return c.id
    async def test_reconcile_stuck_final_preserves_partial_pnl_and_all_publication_decisions(self):
        for status in (CandidateStatus.AWAITING_DECISION,CandidateStatus.APPROVED,CandidateStatus.DECLINED):
            sid=await self.signal(symbol=f'S{self.now.second}USDT'); cid=await self.stuck(sid,status)
            await self.monitor.reconcile_inactive_final_positions(); await self.monitor.reconcile_inactive_final_positions()
            p,m,a,lock=await self.rows(sid)
            self.assertEqual((p.realized_weighted_roi,len(a),lock),(D(50),2,0)); self.assertTrue(p.is_closed)
            async with self.sessions() as s:self.assertIs((await s.get(SignalUpdateCandidateRecord,cid)).status,status)
            if status is CandidateStatus.AWAITING_DECISION:await self.monitor.decide_candidate(cid,self.owner,False)
    async def test_reconcile_competes_with_callback_without_duplicate_accounting(self):
        sid=await self.signal(); cid=await self.stuck(sid)
        results=await asyncio.gather(self.monitor.reconcile_inactive_final_positions(),self.monitor.decide_tp_action(cid,self.owner,PositionActionKind.NORMAL_TP),return_exceptions=True)
        self.assertEqual(len((await self.rows(sid))[2]),2); self.assertEqual((await self.rows(sid))[3],0)
        self.assertTrue((await self.rows(sid))[0].is_closed)
    async def test_reconcile_skips_ambiguous_final_and_cleans_only_proven_terminal_locks(self):
        sid=await self.signal(); await self.stuck(sid)
        async with self.sessions() as s,s.begin():
            e=await s.scalar(select(SignalEventRecord).where(SignalEventRecord.signal_id==sid,SignalEventRecord.tp_index==4)); e.roi_basis_price=D(139)
        summary=await self.monitor.reconcile(); self.assertEqual(summary.inconsistent_final_positions,1)
        self.assertEqual((await self.rows(sid))[3],1); self.assertFalse((await self.rows(sid))[0].is_closed)
        await self.active.cancel_signal(sid,self.owner)
        # Simulate a historical stale lock on an already durably cancelled signal.
        async with self.sessions() as s,s.begin():s.add(DestinationTradeLockRecord(signal_id=sid,destination_id=self.dest,canonical_symbol='BTCUSDT',acquired_at=self.now,updated_at=self.now))
        await self.monitor.reconcile_inactive_final_positions(); self.assertEqual((await self.rows(sid))[3],0)
        open_id=await self.signal(symbol='OPENUSDT'); await self.monitor.reconcile(); self.assertEqual((await self.rows(open_id))[3],1)
    async def test_terminal_releases_symbol_for_another_owner_but_open_trade_blocks(self):
        sid=await self.signal(); other=await self.signal(owner=self.other,root=False)
        async with self.sessions() as s,s.begin():self.assertFalse(await SqlAlchemyPublicationRepository(s).acquire_trade_lock(other,self.dest,'BTCUSDT',self.now))
        await self.close(sid)
        async with self.sessions() as s,s.begin():
            self.assertTrue(await SqlAlchemyPublicationRepository(s).acquire_trade_lock(other,self.dest,'BTCUSDT',self.now))
            self.assertFalse(await SqlAlchemyPublicationRepository(s).acquire_trade_lock(sid,self.dest,'BTCUSDT',self.now))
    async def test_manual_delivery_replies_to_latest_sent_chain_and_retries_without_reclosing(self):
        sid=await self.signal()
        async with self.sessions() as s,s.begin():
            req=SignalCommentRequestRecord(signal_id=sid,signalist_id=self.owner,comment_text='comment',include_snapshot=False,created_at=self.now,updated_at=self.now);s.add(req);await s.flush()
            s.add(SignalCommentPublicationRecord(comment_request_id=req.id,signal_id=sid,destination_id=self.dest,status=PublicationStatus.SENT,reply_to_message_id=10,telegram_message_id=77,sent_at=self.now,created_at=self.now,updated_at=self.now))
        await self.close(sid); pid=(await self.delivery.pending_ids())[0]
        self.publisher.failure=DeliveryRateLimited(1);await self.delivery.process(pid)
        self.publisher.failure=None; self.now+=timedelta(seconds=5);await self.delivery.process(pid)
        self.assertEqual([call[2] for call in self.publisher.calls],[77,77]); self.assertEqual(len((await self.rows(sid))[2]),1)
        async with self.sessions() as s:self.assertIs((await s.get(SignalPositionActionPublicationRecord,pid)).status,PublicationStatus.SENT)
    async def test_delivery_ambiguity_recovery_and_missing_roots_do_not_reopen_or_guess(self):
        for failure in (DeliveryAmbiguous('timeout'),DeliveryRejected('forbidden')):
            sid=await self.signal(symbol=f'D{self.now.second}USDT');await self.close(sid);pid=(await self.delivery.pending_ids())[-1]
            self.publisher.failure=failure;await self.delivery.process(pid);await self.delivery.recover();await self.delivery.process(pid)
            self.assertTrue((await self.rows(sid))[0].is_closed);self.assertEqual((await self.rows(sid))[3],0)
        sid=await self.signal(symbol='NOROOTUSDT',root=False);await self.close(sid)
        async with self.sessions() as s:self.assertIsNone(await s.scalar(select(SignalPositionActionPublicationRecord.id).where(SignalPositionActionPublicationRecord.signal_id==sid)))
    async def test_button_next_to_comment_only_for_supported_open_positions(self):
        sid=await self.signal();view=await self.active.get_open(sid,self.owner)
        rows=detail_keyboard(view,1).inline_keyboard
        self.assertTrue(any([b.text for b in row][:2]==[texts.ACTIVE_SIGNAL_COMMENT,texts.TP_ACTION_FULL_CLOSE_BUTTON] for row in rows))
        for stage in (MonitorStage.DISABLED_LEGACY,MonitorStage.AWAITING_FINAL_TP_ACTION):
            async with self.sessions() as s,s.begin():(await s.get(SignalMonitorStateRecord,sid)).stage=stage
            v=await self.active.get_open(sid,self.owner)
            self.assertFalse(any(b.text==texts.TP_ACTION_FULL_CLOSE_BUTTON for row in detail_keyboard(v,1).inline_keyboard for b in row))
        for actions in (tuple(PositionActionKind),(PositionActionKind.NORMAL_TP,PositionActionKind.FULL_CLOSE)):
            self.assertNotIn('full_close',str(tp_action_keyboard(1,actions).model_dump()))

    async def test_declined_tp_and_post_terminal_invalid_tp_do_not_change_recorded_performance(self):
        sid=await self.signal();await self.poll(sid,'110');cid=await self.candidate(sid)
        async with self.sessions() as s,s.begin():(await s.get(SignalUpdateCandidateRecord,cid)).status=CandidateStatus.DECLINED
        await self.poll(sid,'89')
        async with self.sessions() as s,s.begin():
            events=(await s.scalars(select(SignalEventRecord).where(SignalEventRecord.signal_id==sid))).all()
            before=[(e.id,e.kind,e.roi_basis_price,e.leveraged_roi,e.occurred_at) for e in events]
            # An inconsistent later TP must never replace the valid pre-closure TP.
            s.add(SignalEventRecord(signal_id=sid,kind=SignalEventKind.TP_HIT,tp_index=3,threshold_price=D(130),observed_reference_price=D(130),roi_basis_price=D(130),leveraged_roi=D(60),price_source_type=PriceSourceType.COMPOSITE,occurred_at=self.now+timedelta(seconds=10),created_at=self.now+timedelta(seconds=10)))
        flt=ReportFilter(NOW,self.now+timedelta(seconds=1))
        for _ in range(2):
            report=await self.reports.summary(flt);self.assertEqual((report.wins,report.total_roi),(1,D(20)))
        async with self.sessions() as s:
            events=(await s.scalars(select(SignalEventRecord).where(SignalEventRecord.id.in_([e[0] for e in before])))).all()
            self.assertEqual([(e.id,e.kind,e.roi_basis_price,e.leveraged_roi,e.occurred_at) for e in events],before)
    async def test_reporting_compatibility_incomplete_ledger_and_legacy_no_position(self):
        sid=await self.signal();await self.poll(sid,'110');await self.poll(sid,'89')
        async with self.sessions() as s,s.begin():
            await s.execute(__import__('sqlalchemy').delete(SignalPositionActionRecord).where(SignalPositionActionRecord.signal_id==sid))
        flt=ReportFilter(NOW,self.now+timedelta(seconds=1))
        # Genuine TP qualifies; missing fractions never manufacture a full-size ROI.
        report=await self.reports.summary(flt);self.assertEqual((report.wins,report.total_roi),(1,D(-20)))
        async with self.sessions() as s,s.begin():
            await s.execute(__import__('sqlalchemy').delete(SignalPositionStateRecord).where(SignalPositionStateRecord.signal_id==sid))
        self.assertEqual((await self.reports.summary(flt)).total_roi,D(20))
    async def test_destination_symbol_net_and_pending_cancel_exclusions_use_same_projection(self):
        first=await self.signal();await self.poll(first,'110');await self.poll(first,'89')
        second=await self.signal();await self.poll(second,'89')
        cancelled=await self.signal(symbol='CANCELUSDT');await self.poll(cancelled,'110');await self.active.cancel_signal(cancelled,self.owner)
        pending=await self.signal(symbol='PENDINGUSDT')
        async with self.sessions() as s,s.begin():(await s.get(SignalRecord,pending)).status=SignalLifecycleStatus.PENDING_DETAILS
        report=await self.reports.destination_performance(DestinationReportFilter(self.dest,NOW,self.now+timedelta(seconds=1)))
        self.assertEqual([(r.canonical_symbol,r.roi) for r in report.symbols],[('BTCUSDT',D(0))]);self.assertEqual(report.net_total,D(0))
    async def test_eligibility_rejects_pending_unactivated_and_disabled_legacy(self):
        sid=await self.signal()
        for status in (SignalLifecycleStatus.PENDING_DETAILS,SignalLifecycleStatus.PENDING_ENTRY,SignalLifecycleStatus.ENTRY_TRIGGERED,SignalLifecycleStatus.CANCELLED):
            async with self.sessions() as s,s.begin():
                r=await s.get(SignalRecord,sid);r.status=status;r.cancelled_at=self.now if status is SignalLifecycleStatus.CANCELLED else None
            with self.assertRaises(ValueError):await self.monitor.manual_close_version(sid,self.owner)
        async with self.sessions() as s,s.begin():
            r=await s.get(SignalRecord,sid);r.status=SignalLifecycleStatus.ACTIVE;r.cancelled_at=None
            (await s.get(SignalMonitorStateRecord,sid)).stage=MonitorStage.DISABLED_LEGACY
        with self.assertRaises(ValueError):await self.monitor.manual_close_version(sid,self.owner)
    async def test_stale_monitor_work_cannot_reopen_manual_close(self):
        sid=await self.signal();work=(await self.monitor.load_work(1))[0];await self.close(sid)
        market=MarketDecision(D(140),((Exchange.OURBIT,D(140)),),PriceSourceType.COMPOSITE,self.now)
        self.assertFalse(await self.monitor.evaluate(work,market));self.assertEqual(len((await self.rows(sid))[2]),1)
        self.assertEqual(self.market.calls,1)
    async def test_pending_approved_tp_drains_before_manual_close_without_chain_deadlock(self):
        sid=await self.signal();await self.poll(sid,'110');await self.action(sid,PositionActionKind.NORMAL_TP)
        await self.close(sid);pid=(await self.delivery.pending_ids())[0]
        await self.delivery.process(pid);self.assertEqual(self.publisher.calls,[])
        from chainura.persistence.update_publication_repository import SqlAlchemyUpdatePublicationRepository
        async with self.sessions() as s,s.begin():
            repo=SqlAlchemyUpdatePublicationRepository(s);await repo.plan_pending(self.now)
            updates=await repo.pending_delivery_ids(self.now);self.assertEqual(len(updates),1)
            self.assertIsNotNone(await repo.claim(updates[0],self.now))
            state,parent=await repo.prepare_send(updates[0],self.now);self.assertEqual((state,parent),('sending',10))
            await repo.transition(updates[0],PublicationStatus.SENDING,PublicationStatus.SENT,self.now,message_id=50)
            await repo.complete_request((await s.get(DestinationUpdatePublicationRecord,updates[0])).request_id,self.now)
        await self.delivery.process(pid);self.assertEqual(self.publisher.calls[0][2],50)
    async def test_independent_delivery_restart_unknown_is_never_resent_and_templates_override(self):
        sid=await self.signal();await self.close(sid);pid=(await self.delivery.pending_ids())[0]
        async with self.sessions() as s,s.begin():(await s.get(SignalPositionActionPublicationRecord,pid)).status=PublicationStatus.SENDING
        await self.delivery.recover();await self.delivery.process(pid);self.assertEqual(self.publisher.calls,[])
        async with self.sessions() as s:self.assertIs((await s.get(SignalPositionActionPublicationRecord,pid)).status,PublicationStatus.DELIVERY_UNKNOWN)
        from chainura.domain.message_template import DestinationMessageKind
        templates=DestinationMessageTemplateService(self.tx)
        await templates.set_global(DestinationMessageKind.FULL_CLOSE,'بستن {execution_price}')
        await templates.set_destination(DestinationMessageKind.FULL_CLOSE,self.dest,'بسته شد {symbol} {execution_price} {tp_index}')
        second=await self.signal(symbol='CUSTOMUSDT');await self.close(second);await self.delivery.process((await self.delivery.pending_ids())[0])
        text=self.publisher.calls[0][1];self.assertIn('بسته شد',text);self.assertIn('105',text);self.assertIn('—',text)
    async def test_final_notification_and_duplicate_approval_only_publish_once(self):
        sid=await self.signal();await self.poll(sid,'140')
        async with self.sessions() as s:
            note=await s.scalar(select(SignalistNotificationRecord).join(SignalUpdateCandidateRecord).where(SignalUpdateCandidateRecord.signal_id==sid))
            self.assertEqual(note.interaction_version,3)
        cid=await self.candidate(sid)
        results=await asyncio.gather(*(self.monitor.decide_candidate(cid,self.owner,True) for _ in range(2)),return_exceptions=True)
        self.assertTrue(all(x is CandidateStatus.APPROVED for x in results))
        async with self.sessions() as s:self.assertEqual(await s.scalar(select(func.count()).select_from(UpdatePublicationRequestRecord)),1)

    async def test_cancelled_confirmation_during_fresh_market_call_cannot_close(self):
        sid=await self.signal();version=await self.monitor.manual_close_version(sid,self.owner)
        async def stale():return False
        with self.assertRaises(ValueError):await self.monitor.manual_full_close(sid,self.owner,version,confirmation_current=stale)
        self.assertFalse((await self.rows(sid))[0].is_closed);self.assertEqual((await self.rows(sid))[3],1)

    async def test_publication_cannot_reacquire_lock_from_stale_pre_close_orm_state(self):
        sid=await self.signal()
        async with self.sessions() as session,session.begin():
            test=self
            class RaceSession:
                get=session.get
                scalar=session.scalar
                async def execute(inner,statement):
                    if statement.is_insert and statement.table.name=='destination_trade_locks':
                        await test.close(sid)
                    return await session.execute(statement)
            self.assertFalse(await SqlAlchemyPublicationRepository(RaceSession()).acquire_trade_lock(sid,self.dest,'BTCUSDT',self.now))
        self.assertEqual((await self.rows(sid))[3],0)

if __name__=='__main__':unittest.main()
