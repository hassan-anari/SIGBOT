import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, text

from chainura.application.active_signals import ActiveSignalService
from chainura.application.publication import InitialPublicationService
from chainura.application.signal_service import CreatePendingSignalCommand, SignalService
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType, SignalMode, TradeDirection
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import (DestinationPublicationRecord,
    DestinationTradeLockRecord, InitialPublicationNotificationRecord)
from chainura.domain.publication import PublicationStatus
from chainura.telegram.presentation import texts
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.persistence.signalist_repository import SqlAlchemySignalistRepository
from chainura.domain.signalist import Signalist
from integration_tests.support import create_safe_engine, migrate, require_safe_test_database
from integration_tests.test_phase7_1_staged_signal import SequencePrices, snap


class DestinationTradeLockTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.safe = require_safe_test_database(); migrate("head", cls.safe)

    @classmethod
    def tearDownClass(cls): cls.safe.cleanup()

    async def asyncSetUp(self):
        self.engine=create_safe_engine(self.safe);self.sessions=create_session_factory(self.engine)
        async with self.engine.begin() as c:
            await c.execute(text("DELETE FROM signals"));await c.execute(text("DELETE FROM destinations"));await c.execute(text("DELETE FROM signalists"))
        async with self.sessions() as s,s.begin():
            self.a=await SqlAlchemySignalistRepository(s).add(Signalist.create(81001,"A"))
            self.b=await SqlAlchemySignalistRepository(s).add(Signalist.create(81002,"B"))
        self.tx=SqlAlchemySignalistTransactionFactory(self.sessions)
        self.now=datetime(2026,8,14,tzinfo=timezone.utc)
        async with self.engine.begin() as c:
            self.destination=(await c.execute(text("INSERT INTO destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit,created_at,updated_at) VALUES('x',-81001,'channel','private','active',10,:n,:n) RETURNING id"),{"n":self.now})).scalar_one()
            await c.execute(text("INSERT INTO destination_exchange_priorities(destination_id,exchange,position) VALUES(:d,'ourbit',1)"),{"d":self.destination})
            for actor in (self.a,self.b):await c.execute(text("INSERT INTO destination_signalists(destination_id,signalist_id,created_at) VALUES(:d,:s,:n)"),{"d":self.destination,"s":actor.id,"n":self.now})

    async def asyncTearDown(self): await self.engine.dispose()

    async def signal(self,actor,token,symbol="BTCUSDT",direction=TradeDirection.LONG):
        price=replace(snap("100"),canonical_symbol=symbol,
            native_symbols=((Exchange.OURBIT,symbol),(Exchange.TOOBIT,symbol),(Exchange.LBANK,symbol.lower())))
        command=CreatePendingSignalCommand(actor.id,token,SignalMode.COMPOSITE,symbol,
            symbol.removesuffix("USDT"),"USDT",None,None,direction,10,price)
        return await SignalService(self.tx,SequencePrices(price),lambda:self.now).create_pending(command)

    async def plan(self,signal_id):
        async with self.tx() as tx:
            request=await tx._session.scalar(select(__import__('chainura.persistence.models',fromlist=['InitialPublicationRequestRecord']).InitialPublicationRequestRecord.id).where(__import__('chainura.persistence.models',fromlist=['InitialPublicationRequestRecord']).InitialPublicationRequestRecord.signal_id==signal_id))
            result=await tx.publications.plan(request,self.now);await tx.commit();return result

    async def recover(self):
        await InitialPublicationService(self.tx,None,None,lambda:self.now).recover()

    async def test_symbol_and_destination_admission_is_permanent_and_signal_independent(self):
        first=await self.signal(self.a,"one");second=await self.signal(self.b,"two",direction=TradeDirection.SHORT)
        await self.plan(first.id);await self.plan(second.id)
        async with self.sessions() as s:
            rows=(await s.scalars(select(DestinationPublicationRecord).order_by(DestinationPublicationRecord.signal_id))).all()
            self.assertEqual([r.status.value for r in rows],["pending","superseded"])
            self.assertEqual(rows[1].last_error_category,"destination_trade_lock_occupied")
            self.assertEqual(await s.scalar(select(func.count()).select_from(DestinationTradeLockRecord)),1)
        # The blocked Signal remains cancellable, proving publication admission did not reject it.
        self.assertTrue((await ActiveSignalService(self.tx,lambda:self.now).cancel_signal(second.id,self.b.id)).cancelled)
        self.assertEqual((await self.signal(self.b,"eth","ETHUSDT")).canonical_symbol,"ETHUSDT")

    async def test_real_sqlite_concurrent_planning_has_one_winner(self):
        one,two=await asyncio.gather(self.signal(self.a,"race-a"),self.signal(self.b,"race-b"))
        await asyncio.gather(self.plan(one.id),self.plan(two.id))
        async with self.sessions() as s:
            statuses=(await s.scalars(select(DestinationPublicationRecord.status).where(
                DestinationPublicationRecord.signal_id.in_((one.id,two.id))))).all()
            self.assertEqual(statuses.count(__import__('chainura.domain.publication',fromlist=['PublicationStatus']).PublicationStatus.PENDING),1)
            self.assertEqual(statuses.count(__import__('chainura.domain.publication',fromlist=['PublicationStatus']).PublicationStatus.SUPERSEDED),1)
            self.assertEqual(await s.scalar(select(func.count()).select_from(DestinationTradeLockRecord)),1)
            self.assertEqual(await s.scalar(select(func.count()).select_from(
                __import__('chainura.persistence.models',fromlist=['SignalRecord']).SignalRecord).where(
                __import__('chainura.persistence.models',fromlist=['SignalRecord']).SignalRecord.id.in_((one.id,two.id)))),2)

    async def test_owner_cancellation_releases_but_blocked_row_never_backfills(self):
        owner=await self.signal(self.a,"owner");blocked=await self.signal(self.b,"blocked")
        await self.plan(owner.id);await self.plan(blocked.id)
        self.assertTrue((await ActiveSignalService(self.tx,lambda:self.now).cancel_signal(owner.id,self.a.id)).cancelled)
        fresh=await self.signal(self.a,"fresh");await self.plan(fresh.id)
        async with self.sessions() as s:
            old=await s.scalar(select(DestinationPublicationRecord).where(DestinationPublicationRecord.signal_id==blocked.id))
            new=await s.scalar(select(DestinationPublicationRecord).where(DestinationPublicationRecord.signal_id==fresh.id))
            self.assertEqual(old.status.value,"superseded");self.assertEqual(new.status.value,"pending")

    async def test_recovery_gives_older_legacy_queue_priority(self):
        older=await self.signal(self.a,"legacy-old");newer=await self.signal(self.b,"legacy-new")
        await self.plan(older.id);await self.plan(newer.id)
        # Recreate pre-Phase-3 queued state: neither row has an admission decision.
        async with self.sessions() as s,s.begin():
            await s.execute(__import__('sqlalchemy').delete(DestinationTradeLockRecord))
            rows=(await s.scalars(select(DestinationPublicationRecord).where(
                DestinationPublicationRecord.signal_id.in_((older.id,newer.id))))).all()
            for row in rows:
                row.status=PublicationStatus.PENDING;row.last_error_category=None
        await self.recover()
        async with self.sessions() as s:
            rows=(await s.scalars(select(DestinationPublicationRecord).where(
                DestinationPublicationRecord.signal_id.in_((older.id,newer.id))).order_by(
                DestinationPublicationRecord.id))).all()
            self.assertEqual([r.status for r in rows],[PublicationStatus.PENDING,PublicationStatus.SUPERSEDED])
            self.assertEqual(rows[1].last_error_category,"destination_trade_lock_occupied")
            lock=(await s.scalars(select(DestinationTradeLockRecord))).one()
            self.assertEqual(lock.signal_id,older.id)

    async def test_recovered_legacy_queue_blocks_newer_planning(self):
        older=await self.signal(self.a,"queued-owner");await self.plan(older.id)
        async with self.sessions() as s,s.begin():await s.execute(__import__('sqlalchemy').delete(DestinationTradeLockRecord))
        await self.recover()
        newer=await self.signal(self.b,"after-recovery");await self.plan(newer.id)
        async with self.sessions() as s:
            row=await s.scalar(select(DestinationPublicationRecord).where(
                DestinationPublicationRecord.signal_id==newer.id))
            self.assertIs(row.status,PublicationStatus.SUPERSEDED)

    async def test_visible_legacy_sent_and_unknown_block_but_terminal_does_not(self):
        for index,status in enumerate((PublicationStatus.SENT,PublicationStatus.DELIVERY_UNKNOWN)):
            owner=await self.signal(self.a,f"visible-{index}");await self.plan(owner.id)
            async with self.sessions() as s,s.begin():
                row=await s.scalar(select(DestinationPublicationRecord).where(
                    DestinationPublicationRecord.signal_id==owner.id))
                row.status=status
                if status is PublicationStatus.SENT:
                    row.telegram_message_id=100+index;row.sent_at=self.now
                await s.execute(__import__('sqlalchemy').delete(DestinationTradeLockRecord))
            candidate=await self.signal(self.b,f"visible-candidate-{index}");await self.plan(candidate.id)
            async with self.sessions() as s:
                blocked=await s.scalar(select(DestinationPublicationRecord).where(
                    DestinationPublicationRecord.signal_id==candidate.id))
                self.assertIs(blocked.status,PublicationStatus.SUPERSEDED)
            # Isolate the next visibility state.
            await ActiveSignalService(self.tx,lambda:self.now).cancel_signal(owner.id,self.a.id)
            await ActiveSignalService(self.tx,lambda:self.now).cancel_signal(candidate.id,self.b.id)

    async def test_lock_only_completion_uses_same_privacy_neutral_success_copy(self):
        expected="✅ پردازش مرحله اولیه سیگنال تکمیل شد.\nحالا اطلاعات تکمیلی سیگنال را ثبت کنید."
        self.assertEqual(texts.SIGNAL_INITIAL_PUBLISHED,expected)
        trade="TRD-00000001"
        copies=[]
        for statuses in ((PublicationStatus.SENT,),
                         (PublicationStatus.SENT,PublicationStatus.SUPERSEDED),
                         (PublicationStatus.SUPERSEDED,)):
            visible=[status for status in statuses if status is not PublicationStatus.SUPERSEDED]
            outcome="success" if not visible or all(status is PublicationStatus.SENT for status in visible) else "partial"
            self.assertEqual(outcome,"success")
            copies.append(texts.signal_initial_published(trade))
        self.assertEqual(copies,[copies[0]]*3)
        self.assertIn(trade,copies[0]);self.assertNotIn("مقصد",copies[0])
