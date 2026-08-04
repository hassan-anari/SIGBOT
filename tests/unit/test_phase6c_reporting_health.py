from __future__ import annotations
import unittest
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from chainura.persistence.base import Base
from chainura.persistence.models import *
from chainura.application.reporting import ReportingRepository, ReportingService, period_filter
from chainura.application.health import HealthRepository
from chainura.domain.health import HealthObservation, ServiceHealthStatus, ApiOperation, AdminAlertStatus, AdminAlertKind
from chainura.domain.destination import Exchange, DestinationStatus, DestinationType, DestinationAccessLevel
from chainura.domain.signal import SignalMode, TradeDirection, PriceSourceType, SignalLifecycleStatus
from chainura.domain.signalist import SignalistStatus
from chainura.domain.monitoring import MonitorStage, ResultKind
from chainura.domain.publication import PublicationStatus

class Phase6CReportingHealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as c: await c.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
    async def asyncTearDown(self): await self.engine.dispose()
    async def _seed(self):
        now=datetime(2026,8,4,8,tzinfo=timezone.utc)
        async with self.sessions() as s,s.begin():
            sl=SignalistRecord(telegram_user_id=1,display_name='A',status=SignalistStatus.ARCHIVED,created_at=now,updated_at=now,archived_at=now);s.add(sl);await s.flush()
            dest=DestinationRecord(internal_name='D',telegram_chat_id=-100,destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PUBLIC,status=DestinationStatus.ACTIVE,daily_limit=10,created_at=now,updated_at=now);s.add(dest);await s.flush()
            s1=SignalRecord(signalist_id=sl.id,creation_token='a',mode=SignalMode.COMPOSITE,canonical_symbol='BTCUSDT',base_asset='BTC',quote_asset='USDT',direction=TradeDirection.LONG,entry_price=Decimal('100'),leverage=10,stop_loss=Decimal('90'),reference_price=Decimal('100'),price_source_type=PriceSourceType.COMPOSITE,status=SignalLifecycleStatus.ACTIVE,created_at=now,updated_at=now);s.add(s1);await s.flush()
            s.add(SignalMonitorStateRecord(signal_id=s1.id,stage=MonitorStage.POST_TP1,highest_tp_index=2,current_result_kind=ResultKind.WIN,current_result_price=Decimal('120'),current_leveraged_roi=Decimal('200'),created_at=now,updated_at=now))
            s.add(DestinationPublicationRecord(signal_id=s1.id,destination_id=dest.id,event_kind='initial',selected_exchange=Exchange.OURBIT,status=PublicationStatus.SENT,telegram_message_id=11,sent_at=now,created_at=now,updated_at=now))
        return now,sl.id
    async def test_reporting_summary_and_history_uses_decimal_and_publication_rule(self):
        now,sid=await self._seed(); svc=ReportingService(ReportingRepository(self.sessions)); flt=period_filter('today',now,sid)
        summary=await svc.summary(flt); self.assertEqual(summary.wins,1); self.assertEqual(summary.win_rate,Decimal('100')); self.assertEqual(summary.tp2_count,1); self.assertEqual(summary.confirmed_published_count,1); self.assertEqual(summary.total_roi,Decimal('200'))
        page=await svc.history(flt); self.assertEqual(page.items[0].highest_tp,2); self.assertTrue(page.items[0].confirmed_published)
    async def test_health_down_repeat_recovery_deduplicates(self):
        repo=HealthRepository(self.sessions); now=datetime(2026,8,4,0,tzinfo=timezone.utc)
        await repo.observe(HealthObservation('ourbit_prices',Exchange.OURBIT,ApiOperation.PRICES,ServiceHealthStatus.DOWN,now,'network'))
        await repo.observe(HealthObservation('ourbit_prices',Exchange.OURBIT,ApiOperation.PRICES,ServiceHealthStatus.DOWN,now+timedelta(seconds=1),'network'))
        self.assertEqual(await repo.enqueue_due_repeats(now+timedelta(seconds=61)),1)
        self.assertEqual(await repo.enqueue_due_repeats(now+timedelta(seconds=62)),0)
        await repo.observe(HealthObservation('ourbit_prices',Exchange.OURBIT,ApiOperation.PRICES,ServiceHealthStatus.DEGRADED,now+timedelta(seconds=70),'partial_data'))
        async with self.sessions() as s:
            alerts=(await s.scalars(select(AdminAlertRecord))).all()
            self.assertEqual([a.kind for a in alerts],[AdminAlertKind.OUTAGE_INITIAL,AdminAlertKind.OUTAGE_REPEAT,AdminAlertKind.RECOVERY])
