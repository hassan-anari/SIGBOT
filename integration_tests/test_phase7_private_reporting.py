"""Phase 7 private-reporting acceptance over real SQLite persistence."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chainura.application.reporting import DestinationReportFilter, ReportFilter, ReportingService, ReportingRepository
from chainura.domain.destination import DestinationAccessLevel, DestinationStatus, DestinationType, Exchange
from chainura.domain.monitoring import MonitorStage, ResultKind
from chainura.domain.publication import PublicationStatus
from chainura.domain.signal import PriceSourceType, SignalEntryType, SignalLifecycleStatus, SignalMode, TradeDirection
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.base import Base
from chainura.persistence.models import (DestinationPublicationRecord, DestinationRecord,
    LimitPendingPublicationRecord, SignalMonitorStateRecord, SignalPositionStateRecord,
    SignalRecord, SignalistRecord)
from chainura.telegram.presentation import texts

UTC=timezone.utc
START=datetime(2026,9,1,tzinfo=UTC); END=START+timedelta(days=1)

class Phase7PrivateReportingAcceptance(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        self.service=ReportingService(ReportingRepository(self.sessions))
        async with self.sessions() as s,s.begin():
            a=SignalistRecord(telegram_user_id=71001,display_name='PRIVATE_OWNER_ALPHA',status=SignalistStatus.ACTIVE,created_at=START,updated_at=START)
            b=SignalistRecord(telegram_user_id=71002,display_name='PRIVATE_OWNER_BETA',status=SignalistStatus.ACTIVE,created_at=START,updated_at=START)
            x=DestinationRecord(internal_name='DESTINATION_X_PRIVATE',telegram_chat_id=-10071001,destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PRIVATE,status=DestinationStatus.ACTIVE,daily_limit=20,created_at=START,updated_at=START)
            y=DestinationRecord(internal_name='DESTINATION_Y_PRIVATE',telegram_chat_id=-10071002,destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PRIVATE,status=DestinationStatus.ACTIVE,daily_limit=20,created_at=START,updated_at=START)
            s.add_all((a,b,x,y));await s.flush();self.a,self.b,self.x,self.y=a.id,b.id,x.id,y.id
    async def asyncTearDown(self): await self.engine.dispose()
    async def signal(self,owner,token,created=START,entry=SignalEntryType.MARKET,status=SignalLifecycleStatus.ACTIVE,roi=None,symbol=None):
        async with self.sessions() as s,s.begin():
            asset=(symbol or token).upper()
            row=SignalRecord(signalist_id=owner,creation_token=token,entry_type=entry,mode=SignalMode.COMPOSITE,
                canonical_symbol=asset+'USDT',base_asset=asset,quote_asset='USDT',direction=TradeDirection.LONG,
                entry_price=Decimal('100'),leverage=10,stop_loss=Decimal('90'),reference_price=Decimal('100'),
                price_source_type=PriceSourceType.COMPOSITE,status=status,cancelled_at=created if status is SignalLifecycleStatus.CANCELLED else None,
                created_at=created,updated_at=created);s.add(row);await s.flush()
            if roi is not None:
                value=Decimal(roi);s.add(SignalMonitorStateRecord(signal_id=row.id,stage=MonitorStage.COMPLETED_TARGETS,
                    highest_tp_index=1,current_result_kind=ResultKind.WIN,current_result_price=Decimal('110'),current_leveraged_roi=Decimal('999'),completed_at=created,created_at=created,updated_at=created))
                s.add(SignalPositionStateRecord(signal_id=row.id,remaining_fraction=Decimal('0'),realized_weighted_roi=value,
                    risk_free_used=False,risk_free_active=False,save_profit_50_used=False,effective_stop_price=None,is_closed=True,
                    closed_at=created,version=1,created_at=created,updated_at=created))
            return row.id
    async def market_root(self,signal,destination,status,sent_at=START+timedelta(hours=2),message=100):
        async with self.sessions() as s,s.begin():
            s.add(DestinationPublicationRecord(signal_id=signal,destination_id=destination,event_kind='initial',origin='market',
                selected_exchange=Exchange.OURBIT,status=status,telegram_message_id=message if status is PublicationStatus.SENT else None,
                sent_at=sent_at if status is PublicationStatus.SENT else None,created_at=START,updated_at=START))
    async def limit_root(self,signal,destination,status,sent_at=START+timedelta(hours=3),message=200):
        async with self.sessions() as s,s.begin():
            s.add(LimitPendingPublicationRecord(signal_id=signal,destination_id=destination,selected_exchange=Exchange.TOOBIT,
                status=status,telegram_message_id=message if status is PublicationStatus.SENT else None,
                sent_at=sent_at if status is PublicationStatus.SENT else None,created_at=START,updated_at=START))
    async def test_owner_privacy_definite_roots_sent_at_and_weighted_kpis(self):
        before=await self.signal(self.a,'before',START-timedelta(days=1),roi='8')
        loss=await self.signal(self.a,'loss',roi='-5')
        neutral=await self.signal(self.b,'neutral',roi='0')
        failed=await self.signal(self.a,'failed')
        unknown=await self.signal(self.a,'unknown')
        pending=await self.signal(self.a,'pending')
        limit=await self.signal(self.a,'limit',entry=SignalEntryType.LIMIT,status=SignalLifecycleStatus.CANCELLED)
        outside=await self.signal(self.a,'outside')
        await self.market_root(before,self.x,PublicationStatus.SENT,START+timedelta(hours=1),101)
        await self.market_root(loss,self.x,PublicationStatus.SENT,START+timedelta(hours=2),102)
        await self.market_root(neutral,self.y,PublicationStatus.SENT,START+timedelta(hours=2),103)
        await self.market_root(failed,self.x,PublicationStatus.FAILED)
        await self.market_root(unknown,self.x,PublicationStatus.DELIVERY_UNKNOWN)
        await self.market_root(pending,self.x,PublicationStatus.PENDING)
        await self.limit_root(limit,self.x,PublicationStatus.SENT,START+timedelta(hours=3),201)
        await self.market_root(outside,self.x,PublicationStatus.SENT,END+timedelta(hours=1),104)
        personal=ReportFilter(START-timedelta(days=2),END)
        a_summary=await self.service.signalist_summary(self.a,personal);a_history=await self.service.signalist_history(self.a,personal)
        b_history=await self.service.signalist_history(self.b,personal)
        self.assertEqual(a_summary.total_signals,7);self.assertTrue(all(x.canonical_symbol!='NEUTRALUSDT' for x in a_history.items))
        self.assertEqual([x.canonical_symbol for x in b_history.items],['NEUTRALUSDT'])
        personal_text=texts.private_history_text(a_history)
        for secret in ('DESTINATION_X_PRIVATE','-10071001','sent','ourbit','مقصد','انتشار'):
            self.assertNotIn(secret,personal_text)
        flt=DestinationReportFilter(self.x,START,END)
        summary=await self.service.destination_summary(flt);history=await self.service.destination_history(flt)
        self.assertEqual(summary.total_signals,3);self.assertEqual(summary.cancelled_signals,1)
        self.assertEqual((summary.best_roi,summary.worst_roi),(Decimal('8'),Decimal('-5')))
        self.assertEqual([x.canonical_symbol for x in history.items],['LIMITUSDT','LOSSUSDT','BEFOREUSDT'])
        rendered=texts.private_report_summary_text(summary,texts.DESTINATION_REPORT_TITLE,'DESTINATION_X_PRIVATE')+'\n'+texts.private_history_text(history,destination=True)
        for secret in ('PRIVATE_OWNER_ALPHA','PRIVATE_OWNER_BETA','71001','71002','سیگنالیست'):
            self.assertNotIn(secret,rendered)
        self.assertEqual((await self.service.destination_summary(DestinationReportFilter(self.y,START,END))).total_signals,1)

    async def test_destination_performance_groups_repeated_symbols_and_excludes_non_performance(self):
        btc_win=await self.signal(self.a,'btc-win',roi='10',symbol='btc')
        btc_loss=await self.signal(self.b,'btc-loss',roi='-3.5',symbol='btc')
        eth_loss=await self.signal(self.a,'eth-loss',roi='-4',symbol='eth')
        neutral=await self.signal(self.a,'ong-flat',roi='0',symbol='ong')
        cancelled=await self.signal(
            self.a,'zro-cancelled',status=SignalLifecycleStatus.CANCELLED,roi='99',symbol='zro')
        no_result=await self.signal(self.a,'arb-open',symbol='arb')
        await self.market_root(btc_win,self.x,PublicationStatus.SENT,START+timedelta(hours=1),301)
        await self.market_root(btc_loss,self.x,PublicationStatus.SENT,START+timedelta(hours=2),302)
        await self.market_root(eth_loss,self.x,PublicationStatus.SENT,START+timedelta(hours=3),303)
        await self.market_root(neutral,self.x,PublicationStatus.SENT,START+timedelta(hours=4),304)
        await self.market_root(cancelled,self.x,PublicationStatus.SENT,START+timedelta(hours=5),305)
        await self.market_root(no_result,self.x,PublicationStatus.SENT,START+timedelta(hours=6),306)

        report=await self.service.destination_performance(DestinationReportFilter(self.x,START,END))
        self.assertEqual(
            [(item.canonical_symbol,item.roi) for item in report.symbols],
            [('BTCUSDT',Decimal('6.5')),('ETHUSDT',Decimal('-4')),('ONGUSDT',Decimal('0'))])
        self.assertEqual(
            (report.positive_total,report.negative_total,report.net_total),
            (Decimal('6.5'),Decimal('-4'),Decimal('2.5')))
        rendered=texts.destination_performance_text(
            report,'DESTINATION_X_PRIVATE','2026-09-01','2026-09-01')
        self.assertEqual(rendered.count('BTCUSDT'),1)
        self.assertNotIn('ZROUSDT',rendered);self.assertNotIn('ARBUSDT',rendered)
        for secret in ('PRIVATE_OWNER_ALPHA','PRIVATE_OWNER_BETA','71001','71002','سیگنالیست'):
            self.assertNotIn(secret,rendered)

