from __future__ import annotations
import unittest
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from chainura.persistence.base import Base
from chainura.persistence.models import *
from chainura.application.reporting import ReportingRepository, ReportingService, period_filter
from chainura.application.health import HealthRepository, AdminAlertWorker
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

from market import MarketProbe
from models import Instrument, PriceTick
from chainura.application.market_gateway import SymbolCatalogProvider, BatchedMarketSnapshotProvider, MarketReferencePriceProvider
from chainura.domain.monitoring import MarketDecision
from chainura.application.monitoring import MonitorWork

class FakeAdapter:
    def __init__(self,name, instruments=None, prices=None, fail_instruments=False, fail_prices=False):
        self.name=name; self.instruments=tuple(instruments or ()); self.prices=tuple(prices or ()); self.fail_instruments=fail_instruments; self.fail_prices=fail_prices; self.instrument_calls=0; self.price_calls=0; self.symbol_map={}
    async def fetch_instruments(self):
        self.instrument_calls+=1
        if self.fail_instruments: raise RuntimeError('boom')
        return list(self.instruments)
    async def fetch_prices(self):
        self.price_calls+=1
        if self.fail_prices: raise RuntimeError('boom')
        return list(self.prices)

class Phase6CMarketObservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as c: await c.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        self.now=datetime(2026,8,4,0,tzinfo=timezone.utc)
    async def asyncTearDown(self): await self.engine.dispose()
    def probe(self, adapters):
        p=MarketProbe(clock=lambda:self.now); p.adapters=list(adapters); return p
    def inst(self, ex): return Instrument(ex,'BTCUSDT','BTCUSDT','BTC')
    def tick(self, ex, price): return PriceTick(ex,'BTCUSDT','BTCUSDT',Decimal(price),self.now,'fake')
    async def test_price_failure_one_incident_no_typeerror_and_valid_sources_continue(self):
        adapters=(FakeAdapter('ourbit',[self.inst('ourbit')],fail_prices=True),FakeAdapter('toobit',[self.inst('toobit')],[self.tick('toobit','100')]),FakeAdapter('lbank',[self.inst('lbank')],[self.tick('lbank','100')]))
        p=self.probe(adapters); repo=HealthRepository(self.sessions); catalog=SymbolCatalogProvider(p,repo)
        await catalog._ensure_discovered(); await catalog._fetch_prices(tuple(Exchange))
        async with self.sessions() as s:
            self.assertEqual(await s.scalar(select(func.count()).select_from(OutageIncidentRecord)),1)
        self.assertEqual([a.price_calls for a in adapters],[1,1,1])
    async def test_catalog_failure_independent_incident(self):
        adapters=(FakeAdapter('ourbit',fail_instruments=True),FakeAdapter('toobit',[self.inst('toobit')]),FakeAdapter('lbank',[self.inst('lbank')]))
        p=self.probe(adapters); repo=HealthRepository(self.sessions); catalog=SymbolCatalogProvider(p,repo)
        with self.assertRaises(Exception): await catalog._ensure_discovered()
        async with self.sessions() as s:
            row=await s.get(ServiceHealthRecord,'ourbit_catalog'); self.assertEqual(row.status,ServiceHealthStatus.DOWN)
            self.assertIsNotNone(row.current_incident_id)
            self.assertIsNone(await s.get(ServiceHealthRecord,'ourbit_prices'))
    async def test_health_persistence_failure_does_not_corrupt_price_result(self):
        class BadRepo:
            async def observe(self, obs): raise RuntimeError('db')
        adapters=(FakeAdapter('ourbit',[self.inst('ourbit')],[self.tick('ourbit','100')]),FakeAdapter('toobit',[self.inst('toobit')],[self.tick('toobit','100')]),FakeAdapter('lbank',[self.inst('lbank')],[self.tick('lbank','100')]))
        p=self.probe(adapters); catalog=SymbolCatalogProvider(p,BadRepo()); provider=MarketReferencePriceProvider(p,catalog,BadRepo())
        result=await provider.get_reference_price(mode=SignalMode.COMPOSITE,canonical_symbol='BTCUSDT',selected_exchange=None,native_symbol=None)
        self.assertTrue(result.healthy); self.assertEqual(result.reference_price,Decimal('100'))
    async def test_no_sqlite_operation_while_snapshot_lock_held(self):
        adapters=(FakeAdapter('ourbit',[self.inst('ourbit')],[self.tick('ourbit','100')]),)
        p=self.probe(adapters); repo=HealthRepository(self.sessions); catalog=SymbolCatalogProvider(p,repo)
        await catalog._ensure_exchange(Exchange.OURBIT)
        original=repo.observe; seen=[]
        async def wrapped(obs):
            seen.append(catalog._snapshot_lock.locked())
            return await original(obs)
        repo.observe=wrapped
        await catalog._fetch_prices((Exchange.OURBIT,))
        self.assertEqual(seen,[False])
    async def test_persisted_composite_survives_catalog_outage_with_native_mappings(self):
        adapters=(FakeAdapter('ourbit',fail_instruments=True,prices=[self.tick('ourbit','110')]),FakeAdapter('toobit',[self.inst('toobit')],[self.tick('toobit','110')]),FakeAdapter('lbank',[self.inst('lbank')],[self.tick('lbank','110')]))
        p=self.probe(adapters); repo=HealthRepository(self.sessions); catalog=SymbolCatalogProvider(p,repo)
        with self.assertRaises(Exception):
            await catalog._ensure_discovered()
        provider=BatchedMarketSnapshotProvider(p,catalog,repo)
        work=MonitorWork(1,1,SignalMode.COMPOSITE,'BTCUSDT',None,TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('90'),PriceSourceType.COMPOSITE,((Exchange.OURBIT,'BTCUSDT'),(Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'BTCUSDT')),self.now)
        result=await provider.snapshot((work,))
        self.assertEqual(result[1].reference_price,Decimal('110'))
        self.assertEqual(len(result[1].source_prices),3)
        async with self.sessions() as s:
            self.assertIsNotNone((await s.get(ServiceHealthRecord,'ourbit_catalog')).current_incident_id)
            self.assertEqual((await s.get(ServiceHealthRecord,'ourbit_prices')).status,ServiceHealthStatus.HEALTHY)
            self.assertEqual((await s.get(ServiceHealthRecord,'toobit_prices')).status,ServiceHealthStatus.HEALTHY)
    async def test_selected_price_failure_skips_single_observes_price_and_keeps_unrelated_signal(self):
        adapters=(FakeAdapter('ourbit',[self.inst('ourbit')],fail_prices=True),FakeAdapter('toobit',[self.inst('toobit')],[self.tick('toobit','111')]),FakeAdapter('lbank',[self.inst('lbank')],[self.tick('lbank','111')]))
        p=self.probe(adapters); repo=HealthRepository(self.sessions); catalog=SymbolCatalogProvider(p,repo); provider=BatchedMarketSnapshotProvider(p,catalog,repo)
        single=MonitorWork(1,1,SignalMode.SINGLE_EXCHANGE,'BTCUSDT',Exchange.OURBIT,TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('90'),PriceSourceType.OURBIT_ONLY,((Exchange.OURBIT,'BTCUSDT'),),self.now)
        composite=MonitorWork(2,1,SignalMode.COMPOSITE,'BTCUSDT',None,TradeDirection.LONG,Decimal('100'),10,(Decimal('110'),),Decimal('90'),PriceSourceType.COMPOSITE,((Exchange.TOOBIT,'BTCUSDT'),(Exchange.LBANK,'BTCUSDT')),self.now)
        result=await provider.snapshot((single,composite))
        self.assertNotIn(1,result); self.assertIn(2,result)
        self.assertEqual([a.instrument_calls for a in adapters],[0,0,0])
        self.assertEqual([a.price_calls for a in adapters],[1,1,1])
        async with self.sessions() as s:
            price=await s.get(ServiceHealthRecord,'ourbit_prices'); self.assertEqual(price.status,ServiceHealthStatus.DOWN)
            self.assertIsNone(await s.get(ServiceHealthRecord,'ourbit_catalog'))

class Phase6CAlertLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as c: await c.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        self.now=datetime(2026,8,4,0,tzinfo=timezone.utc)
    async def asyncTearDown(self): await self.engine.dispose()
    async def test_recovery_supersedes_pending_repeat_but_not_sending(self):
        repo=HealthRepository(self.sessions)
        await repo.observe(HealthObservation('ourbit_prices',Exchange.OURBIT,ApiOperation.PRICES,ServiceHealthStatus.DOWN,self.now,'network'))
        await repo.enqueue_due_repeats(self.now+timedelta(seconds=61))
        async with self.sessions() as s,s.begin():
            repeat=(await s.scalars(select(AdminAlertRecord).where(AdminAlertRecord.kind==AdminAlertKind.OUTAGE_REPEAT))).first(); repeat.status=AdminAlertStatus.SENDING
        await repo.enqueue_due_repeats(self.now+timedelta(seconds=122))
        await repo.observe(HealthObservation('ourbit_prices',Exchange.OURBIT,ApiOperation.PRICES,ServiceHealthStatus.HEALTHY,self.now+timedelta(seconds=130),None))
        async with self.sessions() as s:
            alerts=(await s.scalars(select(AdminAlertRecord).order_by(AdminAlertRecord.kind,AdminAlertRecord.repeat_sequence))).all()
            repeats=[a for a in alerts if a.kind is AdminAlertKind.OUTAGE_REPEAT]
            self.assertEqual(repeats[0].status,AdminAlertStatus.SENDING)
            self.assertEqual(repeats[1].status,AdminAlertStatus.SUPERSEDED)
            self.assertEqual(len([a for a in alerts if a.kind is AdminAlertKind.RECOVERY]),1)
        self.assertEqual(await repo.enqueue_due_repeats(self.now+timedelta(seconds=200)),0)
    async def test_timeout_delivery_unknown_is_not_resent_and_recovery_delivers(self):
        repo=HealthRepository(self.sessions); sent=[]
        await repo.observe(HealthObservation('ourbit_prices',Exchange.OURBIT,ApiOperation.PRICES,ServiceHealthStatus.DOWN,self.now,'network'))
        class TimeoutTransport:
            async def send_message(self,admin_id,text): raise TimeoutError()
        worker=AdminAlertWorker(repo,TimeoutTransport(),1,clock=lambda:self.now)
        due=await repo.due_alerts(self.now,1); await worker._send(due[0].id)
        async with self.sessions() as s:
            first=await s.get(AdminAlertRecord,due[0].id); self.assertEqual(first.status,AdminAlertStatus.DELIVERY_UNKNOWN)
        self.assertEqual(await repo.due_alerts(self.now,10),())
        await repo.observe(HealthObservation('ourbit_prices',Exchange.OURBIT,ApiOperation.PRICES,ServiceHealthStatus.HEALTHY,self.now+timedelta(seconds=10),None))
        class OkTransport:
            async def send_message(self,admin_id,text): sent.append(text); return 22
        worker=AdminAlertWorker(repo,OkTransport(),1,clock=lambda:self.now+timedelta(seconds=11))
        due=await repo.due_alerts(self.now+timedelta(seconds=11),1); await worker._send(due[0].id)
        async with self.sessions() as s:
            rec=await s.get(AdminAlertRecord,due[0].id); self.assertEqual(rec.status,AdminAlertStatus.SENT); self.assertEqual(rec.telegram_message_id,22)
        self.assertIn('مدت اختلال', sent[0])

class Phase6CHealthSeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as c: await c.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
    async def asyncTearDown(self): await self.engine.dispose()
    async def test_seed_materializes_all_six_unknown_services(self):
        repo=HealthRepository(self.sessions); await repo.seed_unknown_services(datetime(2026,8,4,tzinfo=timezone.utc))
        rows=await repo.list_health(); self.assertEqual(len(rows),6); self.assertEqual({r.status for r in rows},{ServiceHealthStatus.UNKNOWN})

class Phase6CCustomReportStateTests(unittest.TestCase):
    def test_custom_refresh_reuses_all_scope_range(self):
        from chainura.telegram.admin_routes import _report_state_filter
        data={"report_period":"custom","report_start":"2026-08-03T20:30:00+00:00","report_end":"2026-08-04T20:30:00+00:00","report_signalist_id":None}
        flt=_report_state_filter(data)
        self.assertEqual((flt.start_utc.isoformat(),flt.end_utc.isoformat(),flt.signalist_id),("2026-08-03T20:30:00+00:00","2026-08-04T20:30:00+00:00",None))
    def test_custom_refresh_reuses_individual_scope_range(self):
        from chainura.telegram.admin_routes import _report_state_filter
        data={"report_period":"custom","report_start":"2026-08-01T20:30:00+00:00","report_end":"2026-08-08T20:30:00+00:00","report_signalist_id":42}
        flt=_report_state_filter(data)
        self.assertEqual((flt.start_utc.isoformat(),flt.end_utc.isoformat(),flt.signalist_id),("2026-08-01T20:30:00+00:00","2026-08-08T20:30:00+00:00",42))
    def test_custom_history_back_uses_same_stored_range(self):
        from chainura.telegram.admin_routes import _report_state_filter
        data={"report_period":"custom","report_start":"2026-07-31T20:30:00+00:00","report_end":"2026-08-02T20:30:00+00:00","report_signalist_id":7}
        first=_report_state_filter(data); second=_report_state_filter(data)
        self.assertEqual(first,second)
    def test_malformed_callbacks_and_ranges_fail_safely(self):
        from chainura.telegram.admin_routes import _positive_int, _report_state_filter
        self.assertIsNone(_positive_int("x"))
        self.assertIsNone(_positive_int("0"))
        self.assertIsNone(_report_state_filter({"report_start":"bad","report_end":"2026-08-04T00:00:00+00:00"}))
