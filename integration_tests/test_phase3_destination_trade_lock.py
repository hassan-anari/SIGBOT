import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, text

from chainura.application.active_signals import ActiveSignalService
from chainura.application.signal_service import CreatePendingSignalCommand, SignalService
from chainura.domain.destination import Exchange
from chainura.domain.signal import PriceSourceType, SignalMode, TradeDirection
from chainura.persistence.database import create_session_factory
from chainura.persistence.models import (DestinationPublicationRecord,
    DestinationTradeLockRecord)
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

    async def test_owner_cancellation_releases_but_blocked_row_never_backfills(self):
        owner=await self.signal(self.a,"owner");blocked=await self.signal(self.b,"blocked")
        await self.plan(owner.id);await self.plan(blocked.id)
        self.assertTrue((await ActiveSignalService(self.tx,lambda:self.now).cancel_signal(owner.id,self.a.id)).cancelled)
        fresh=await self.signal(self.a,"fresh");await self.plan(fresh.id)
        async with self.sessions() as s:
            old=await s.scalar(select(DestinationPublicationRecord).where(DestinationPublicationRecord.signal_id==blocked.id))
            new=await s.scalar(select(DestinationPublicationRecord).where(DestinationPublicationRecord.signal_id==fresh.id))
            self.assertEqual(old.status.value,"superseded");self.assertEqual(new.status.value,"pending")
