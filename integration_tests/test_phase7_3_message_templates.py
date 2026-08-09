import unittest
from sqlalchemy import delete, select

from integration_tests.support import create_safe_engine, migrate, require_safe_test_database
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.domain.message_template import DestinationMessageKind as Kind, MessageTemplateSource as Source
from chainura.persistence.models import DestinationMessageTemplateRecord, DestinationRecord
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.persistence.database import create_session_factory


class MessageTemplateIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.safe=require_safe_test_database();migrate("head",self.safe)
        self.engine=create_safe_engine(self.safe);self.sessions=create_session_factory(self.engine)
        self.factory=SqlAlchemySignalistTransactionFactory(self.sessions);self.service=DestinationMessageTemplateService(self.factory)
        async with self.factory() as tx:
            tx._session.add_all([DestinationRecord(internal_name="A",telegram_chat_id=-1,destination_type="channel",access_level="public",status="active",daily_limit=2),DestinationRecord(internal_name="B",telegram_chat_id=-2,destination_type="channel",access_level="private",status="active",daily_limit=2)])
            await tx.commit()

    async def asyncTearDown(self):
        await self.engine.dispose();self.safe.cleanup()

    async def test_precedence_removal_and_independent_destinations(self):
        context={"symbol":"BTC","tp_index":"2"}
        result=await self.service.resolve(Kind.TP_HIT,destination_id=1,context=context);self.assertEqual(result.source,Source.BUILTIN)
        await self.service.set_global(Kind.TP_HIT,"global {symbol}")
        await self.service.set_destination(Kind.TP_HIT,1,"A {symbol}")
        await self.service.set_destination(Kind.TP_HIT,2,"B {symbol}")
        self.assertEqual((await self.service.resolve(Kind.TP_HIT,destination_id=1,context=context)).text,"A BTC")
        self.assertEqual((await self.service.resolve(Kind.TP_HIT,destination_id=2,context=context)).text,"B BTC")
        await self.service.remove_destination(Kind.TP_HIT,1)
        self.assertEqual((await self.service.resolve(Kind.TP_HIT,destination_id=1,context=context)).text,"global BTC")
        await self.service.remove_global(Kind.TP_HIT)
        self.assertEqual((await self.service.resolve(Kind.TP_HIT,destination_id=1,context=context)).source,Source.BUILTIN)

    async def test_destination_delete_cascades_but_global_remains(self):
        await self.service.set_global(Kind.SL_HIT,"global")
        await self.service.set_destination(Kind.SL_HIT,1,"specific")
        async with self.factory() as tx:
            await tx._session.execute(delete(DestinationRecord).where(DestinationRecord.id==1));await tx.commit()
        async with self.factory() as tx:
            rows=(await tx._session.scalars(select(DestinationMessageTemplateRecord))).all();await tx.commit()
        self.assertEqual(len(rows),1);self.assertIsNone(rows[0].destination_id)
