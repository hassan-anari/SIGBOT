import unittest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from chainura.application.admin_signalist_service import AdminSignalistService
from chainura.application.pagination import PageRequest
from chainura.application.destination_service import DestinationService
from chainura.application.destination_assignment_service import DestinationAssignmentService
from chainura.application.signalist_service import SignalistService
from chainura.domain.destination import DestinationAccessLevel,DestinationType,Exchange
from chainura.errors import (DestinationAssignmentNotFoundError, DestinationPersistenceError,
    DuplicateDestinationChatIdError,DuplicateDestinationInternalNameError,
    DuplicateDestinationAssignmentError, SignalistNotEligibleForAssignmentError)
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
        await self.service.deactivate(d.id); await self.service.activate(d.id)
        self.assertEqual((await self.service.detail(d.id)).assigned_count,1)
        self.assertEqual((await self.service.detail(d.id)).priority.exchanges,(Exchange.OURBIT,))
        with self.assertRaises(DuplicateDestinationAssignmentError): await assignments.assign(d.id,signalist.id)

    async def test_metadata_update_and_duplicate_translation(self):
        first=(await self.service.create("A",-100,DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,3,(Exchange.OURBIT,))).destination
        second=(await self.service.create("B",-101,DestinationType.GROUP,DestinationAccessLevel.PUBLIC,2,(Exchange.LBANK,))).destination
        updated=await self.service.update_metadata(first.id,"C",DestinationType.GROUP,DestinationAccessLevel.PUBLIC,9,first.updated_at)
        self.assertEqual((updated.internal_name,updated.destination_type,updated.access_level,updated.daily_limit),("C",DestinationType.GROUP,DestinationAccessLevel.PUBLIC,9))
        with self.assertRaises(DuplicateDestinationInternalNameError):
            await self.service.update_metadata(second.id,"C",DestinationType.GROUP,DestinationAccessLevel.PUBLIC,2,second.updated_at)

    async def test_stale_metadata_and_priority_are_controlled(self):
        item=(await self.service.create("A",-100,DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,3,(Exchange.OURBIT,))).destination
        changed=await self.service.update_metadata(item.id,"B",DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,3,item.updated_at)
        with self.assertRaises(DestinationPersistenceError):
            await self.service.update_metadata(item.id,"C",DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,3,item.updated_at)
        replaced=await self.service.replace_priority(item.id,(Exchange.LBANK,Exchange.TOOBIT),changed.updated_at)
        self.assertEqual(replaced.priority.exchanges,(Exchange.LBANK,Exchange.TOOBIT))
        with self.assertRaises(DestinationPersistenceError):
            await self.service.replace_priority(item.id,(Exchange.OURBIT,),changed.updated_at)

    async def test_assignment_eligibility_unassignment_and_reverse_listing(self):
        signalists=SignalistService(self.factory); active=await signalists.create(91,"Active"); inactive=await signalists.create(92,"Inactive"); archived=await signalists.create(93,"Archived")
        inactive=await signalists.deactivate(inactive.id); archived=await signalists.archive(archived.id)
        destination=(await self.service.create("A",-100,DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,3,(Exchange.OURBIT,))).destination
        assignments=DestinationAssignmentService(self.factory)
        eligible=await assignments.list_eligible(destination.id,PageRequest(1)); self.assertEqual({x.id for x in eligible.items},{active.id,inactive.id})
        await assignments.assign(destination.id,active.id); await assignments.assign(destination.id,inactive.id)
        with self.assertRaises(SignalistNotEligibleForAssignmentError): await assignments.assign(destination.id,archived.id)
        reverse=await AdminSignalistService((999,),signalists,self.factory).assigned_destinations(active.id,PageRequest(1))
        self.assertEqual([x.id for x in reverse.items],[destination.id])
        await assignments.unassign(destination.id,active.id)
        with self.assertRaises(DestinationAssignmentNotFoundError): await assignments.unassign(destination.id,active.id)

    async def test_database_priority_and_foreign_key_constraints(self):
        destination=(await self.service.create("A",-100,DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,3,(Exchange.OURBIT,))).destination
        async with self.engine.begin() as connection:
            with self.assertRaises(IntegrityError):
                await connection.execute(text("INSERT INTO destination_exchange_priorities(destination_id,exchange,position) VALUES (:id,'lbank',1)"),{"id":destination.id})
        async with self.engine.begin() as connection:
            with self.assertRaises(IntegrityError):
                await connection.execute(text("INSERT INTO destination_signalists(destination_id,signalist_id) VALUES (:id,99999)"),{"id":destination.id})

    async def test_persistence_after_engine_recreation(self):
        destination=(await self.service.create("Persist",-100,DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,3,(Exchange.OURBIT,Exchange.LBANK))).destination
        await self.engine.dispose(); self.engine=create_safe_engine(self.safe)
        self.factory=SqlAlchemySignalistTransactionFactory(create_session_factory(self.engine)); self.service=DestinationService(self.factory)
        detail=await self.service.detail(destination.id)
        self.assertEqual(detail.destination.internal_name,"Persist"); self.assertEqual(detail.priority.exchanges,(Exchange.OURBIT,Exchange.LBANK))
