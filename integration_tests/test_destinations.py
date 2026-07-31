import unittest
from sqlalchemy import text
from chainura.application.destination_service import DestinationService
from chainura.application.destination_assignment_service import DestinationAssignmentService
from chainura.application.signalist_service import SignalistService
from chainura.domain.destination import DestinationAccessLevel,DestinationType,Exchange
from chainura.errors import DuplicateDestinationChatIdError,DuplicateDestinationInternalNameError,DuplicateDestinationAssignmentError
from chainura.persistence.database import create_session_factory
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from integration_tests.support import require_safe_test_database,create_safe_engine,migrate

class DestinationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.safe=require_safe_test_database(); migrate("head",self.safe); self.engine=create_safe_engine(self.safe)
        self.factory=SqlAlchemySignalistTransactionFactory(create_session_factory(self.engine)); self.service=DestinationService(self.factory)
    async def asyncTearDown(self): await self.engine.dispose(); self.safe.cleanup()
    async def test_create_priority_unique_and_persistent(self):
        result=await self.service.create("A",-100,DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,3,(Exchange.OURBIT,Exchange.LBANK))
        self.assertEqual(result.priority.exchanges,(Exchange.OURBIT,Exchange.LBANK)); self.assertEqual((await self.service.detail(result.destination.id)).destination.internal_name,"A")
        with self.assertRaises(DuplicateDestinationChatIdError): await self.service.create("B",-100,DestinationType.GROUP,DestinationAccessLevel.PUBLIC,1,(Exchange.TOOBIT,))
        with self.assertRaises(DuplicateDestinationInternalNameError): await self.service.create("A",-101,DestinationType.GROUP,DestinationAccessLevel.PUBLIC,1,(Exchange.TOOBIT,))
    async def test_status_preserves_priority_and_assignment(self):
        signalist=await SignalistService(self.factory).create(99,"S")
        d=(await self.service.create("A",-100,DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,3,(Exchange.OURBIT,))).destination
        assignments=DestinationAssignmentService(self.factory); await assignments.assign(d.id,signalist.id)
        await self.service.activate(d.id); await self.service.deactivate(d.id); await self.service.activate(d.id)
        self.assertEqual((await self.service.detail(d.id)).assigned_count,1)
        self.assertEqual((await self.service.detail(d.id)).priority.exchanges,(Exchange.OURBIT,))
        with self.assertRaises(DuplicateDestinationAssignmentError): await assignments.assign(d.id,signalist.id)
