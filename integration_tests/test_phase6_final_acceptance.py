from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chainura.application.health import AdminAlertWorker, HealthRepository
from chainura.application.reporting import ReportingRepository, ReportingService, period_filter
from chainura.domain.destination import DestinationAccessLevel, DestinationStatus, DestinationType, Exchange
from chainura.domain.health import AdminAlertKind, AdminAlertStatus, ApiOperation, HealthObservation, ServiceHealthStatus
from chainura.domain.monitoring import MonitorStage, ResultKind
from chainura.domain.publication import PublicationStatus
from chainura.domain.signal import PriceSourceType, SignalLifecycleStatus, SignalMode, TradeDirection
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.base import Base
from chainura.persistence.models import *

class FakeTelegram:
    def __init__(self, fail=()): self.fail=list(fail); self.messages=[]; self.next_id=100
    async def send_message(self, admin_id, text):
        self.messages.append(text)
        if self.fail:
            item=self.fail.pop(0)
            if item=='timeout': raise TimeoutError()
        self.next_id+=1
        return self.next_id

class Phase6FinalAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as c: await c.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        self.now=datetime(2026,8,4,8,tzinfo=timezone.utc)
        self.health=HealthRepository(self.sessions); self.reporting=ReportingService(ReportingRepository(self.sessions))
    async def asyncTearDown(self): await self.engine.dispose()
    async def signalist(self,name='S',status=SignalistStatus.ACTIVE):
        async with self.sessions() as s,s.begin():
            sl=SignalistRecord(telegram_user_id=1000+hash(name)%100000,display_name=name,status=status,created_at=self.now,updated_at=self.now,archived_at=self.now if status is SignalistStatus.ARCHIVED else None);s.add(sl);await s.flush();return sl.id
    async def destination(self):
        async with self.sessions() as s,s.begin():
            d=DestinationRecord(internal_name='D',telegram_chat_id=-100,destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PUBLIC,status=DestinationStatus.ACTIVE,daily_limit=10,created_at=self.now,updated_at=self.now);s.add(d);await s.flush();return d.id
    async def signal(self,sl,mode=SignalMode.COMPOSITE,created=None,token='x'):
        created=created or self.now
        async with self.sessions() as s,s.begin():
            sig=SignalRecord(signalist_id=sl,creation_token=token,mode=mode,canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',selected_exchange=None if mode is SignalMode.COMPOSITE else Exchange.OURBIT,direction=TradeDirection.LONG,entry_price=Decimal('100'),leverage=10,stop_loss=Decimal('90'),reference_price=Decimal('100'),price_source_type=PriceSourceType.COMPOSITE if mode is SignalMode.COMPOSITE else PriceSourceType.OURBIT_ONLY,status=SignalLifecycleStatus.ACTIVE,created_at=created,updated_at=created);s.add(sig);await s.flush();return sig.id
    async def state(self,sig,stage,result=None,tp=0,roi=None):
        async with self.sessions() as s,s.begin():
            s.add(SignalMonitorStateRecord(signal_id=sig,stage=stage,highest_tp_index=tp,current_result_kind=result,current_result_price=Decimal('100') if result else None,current_leveraged_roi=roi,created_at=self.now,updated_at=self.now,completed_at=self.now if stage in (MonitorStage.COMPLETED_SL,MonitorStage.COMPLETED_BREAKEVEN,MonitorStage.COMPLETED_TARGETS) else None))
    async def published(self,sig,dest):
        async with self.sessions() as s,s.begin():
            s.add(DestinationPublicationRecord(signal_id=sig,destination_id=dest,event_kind='initial',selected_exchange=Exchange.OURBIT,status=PublicationStatus.SENT,telegram_message_id=10+sig,sent_at=self.now,created_at=self.now,updated_at=self.now))
    async def report(self,period='today',sid=None):
        return await self.reporting.summary(period_filter(period,self.now,sid))

    async def test_A_composite_tp1_tp2_reporting_and_restart_no_duplicate(self):
        sl=await self.signalist('active'); d=await self.destination(); sig=await self.signal(sl,token='a')
        await self.published(sig,d); await self.state(sig,MonitorStage.POST_TP1,ResultKind.WIN,2,Decimal('200'))
        summary=await self.report('today',sl); self.assertEqual((summary.wins,summary.tp2_count,summary.confirmed_published_count,summary.composite_count),(1,1,1,1))
        again=await self.report('today',sl); self.assertEqual(again.total_roi,Decimal('200'))
    async def test_B_tp_supersession_shape(self):
        sl=await self.signalist('tp'); d=await self.destination(); sig=await self.signal(sl,token='b'); await self.published(sig,d); await self.state(sig,MonitorStage.POST_TP1,ResultKind.WIN,2,Decimal('200'))
        async with self.sessions() as s,s.begin():
            e1=SignalEventRecord(signal_id=sig,kind=SignalEventKind.TP_HIT,tp_index=1,threshold_price=Decimal('110'),observed_reference_price=Decimal('120'),roi_basis_price=Decimal('110'),leveraged_roi=Decimal('100'),price_source_type=PriceSourceType.COMPOSITE,occurred_at=self.now,created_at=self.now);s.add(e1);await s.flush()
            e2=SignalEventRecord(signal_id=sig,kind=SignalEventKind.TP_HIT,tp_index=2,threshold_price=Decimal('120'),observed_reference_price=Decimal('120'),roi_basis_price=Decimal('120'),leveraged_roi=Decimal('200'),price_source_type=PriceSourceType.COMPOSITE,occurred_at=self.now,created_at=self.now);s.add(e2);await s.flush()
            c1=SignalUpdateCandidateRecord(signal_id=sig,event_id=e1.id,signalist_id=sl,status=CandidateStatus.SUPERSEDED,superseded_by_event_id=e2.id,created_at=self.now,updated_at=self.now);s.add(c1)
        summary=await self.report('today',sl); self.assertEqual(summary.tp2_count,1)
    async def test_C_sl_and_breakeven_reporting(self):
        sl=await self.signalist('mix'); loss=await self.signal(sl,token='c1'); be=await self.signal(sl,token='c2')
        await self.state(loss,MonitorStage.COMPLETED_SL,ResultKind.LOSS,0,Decimal('-100')); await self.state(be,MonitorStage.COMPLETED_BREAKEVEN,ResultKind.NEUTRAL,0,Decimal('0'))
        summary=await self.report('today',sl); self.assertEqual((summary.losses,summary.breakevens,summary.win_rate,summary.total_roi),(1,1,Decimal('0'),Decimal('-100')))
    async def test_D_partial_outage_repeat_recovery_and_no_reconstruction(self):
        await self.health.observe(HealthObservation('ourbit_prices',Exchange.OURBIT,ApiOperation.PRICES,ServiceHealthStatus.DOWN,self.now,'network'))
        self.assertEqual(await self.health.enqueue_due_repeats(self.now+timedelta(seconds=60)),1)
        await self.health.observe(HealthObservation('ourbit_prices',Exchange.OURBIT,ApiOperation.PRICES,ServiceHealthStatus.DEGRADED,self.now+timedelta(seconds=70),'partial_data'))
        async with self.sessions() as s:
            self.assertEqual(await s.scalar(select(func.count()).select_from(OutageIncidentRecord)),1)
            self.assertEqual(await s.scalar(select(func.count()).select_from(AdminAlertRecord).where(AdminAlertRecord.kind==AdminAlertKind.RECOVERY)),1)
    async def test_E_total_outage_restart_no_duplicate_initial_or_burst(self):
        for ex in Exchange: await self.health.observe(HealthObservation(f'{ex.value}_prices',ex,ApiOperation.PRICES,ServiceHealthStatus.DOWN,self.now,'network'))
        self.assertEqual(await self.health.enqueue_due_repeats(self.now+timedelta(minutes=5)),3)
        self.assertEqual(await self.health.enqueue_due_repeats(self.now+timedelta(minutes=5,seconds=1)),0)
        async with self.sessions() as s: self.assertEqual(await s.scalar(select(func.count()).select_from(OutageIncidentRecord)),3)
    async def test_F_catalog_outage_independent_and_recovery(self):
        await self.health.observe(HealthObservation('toobit_catalog',Exchange.TOOBIT,ApiOperation.CATALOG,ServiceHealthStatus.DOWN,self.now,'network'))
        await self.health.observe(HealthObservation('toobit_prices',Exchange.TOOBIT,ApiOperation.PRICES,ServiceHealthStatus.HEALTHY,self.now,None))
        await self.health.observe(HealthObservation('toobit_catalog',Exchange.TOOBIT,ApiOperation.CATALOG,ServiceHealthStatus.HEALTHY,self.now+timedelta(seconds=5),None))
        async with self.sessions() as s:
            cat=await s.get(ServiceHealthRecord,'toobit_catalog'); price=await s.get(ServiceHealthRecord,'toobit_prices')
            self.assertEqual((cat.status,price.status),(ServiceHealthStatus.HEALTHY,ServiceHealthStatus.HEALTHY))
    async def test_G_alert_ambiguity_next_repeat_and_recovery(self):
        await self.health.observe(HealthObservation('lbank_prices',Exchange.LBANK,ApiOperation.PRICES,ServiceHealthStatus.DOWN,self.now,'network'))
        tg=FakeTelegram(fail=('timeout',)); worker=AdminAlertWorker(self.health,tg,1,clock=lambda:self.now)
        due=await self.health.due_alerts(self.now,1); await worker._send(due[0].id)
        self.assertEqual(await self.health.enqueue_due_repeats(self.now+timedelta(seconds=60)),1)
        await self.health.observe(HealthObservation('lbank_prices',Exchange.LBANK,ApiOperation.PRICES,ServiceHealthStatus.HEALTHY,self.now+timedelta(seconds=70),None))
        async with self.sessions() as s:
            statuses=[a.status for a in (await s.scalars(select(AdminAlertRecord).order_by(AdminAlertRecord.id))).all()]
            self.assertIn(AdminAlertStatus.DELIVERY_UNKNOWN,statuses); self.assertIn(AdminAlertStatus.PENDING,statuses)
    async def test_H_reporting_boundaries_scopes_pagination_no_market_calls(self):
        a=await self.signalist('archived',SignalistStatus.ARCHIVED); d=await self.destination()
        for i in range(12):
            sig=await self.signal(a,created=self.now-timedelta(days=i),token=f'h{i}'); await self.state(sig,MonitorStage.POST_TP1,ResultKind.WIN,1,Decimal('10')); await self.published(sig,d)
        today=await self.report('today',a); week=await self.report('7d',a); month=await self.report('30d',a)
        self.assertEqual((today.total_signals,week.total_signals,month.total_signals),(1,7,12))
        flt=period_filter('30d',self.now,a); page1=await self.reporting.history(flt,1); page2=await self.reporting.history(flt,2)
        self.assertEqual((len(page1.items),len(page2.items),page1.pages),(10,2,2))

class Phase6ManualChecklistTests(unittest.TestCase):
    def test_manual_checklist_exists(self):
        text=Path('docs/PHASE6_ACCEPTANCE.md').read_text(encoding='utf-8')
        for phrase in ['TP1/TP2 Reply Chain','Breakeven','Partial outage','Repeat after one minute','Recovery','Restart/no-duplicate','Database rows']:
            self.assertIn(phrase,text)
