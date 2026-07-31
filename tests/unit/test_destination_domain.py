import unittest
from datetime import datetime, timezone
from chainura.domain.destination import *
from chainura.errors import (InvalidDestinationInputError,
    InvalidDestinationStatusTransitionError, InvalidExchangePriorityError)

class DestinationDomainTests(unittest.TestCase):
    def make(self): return Destination.create("  Alpha  ",-100,DestinationType.CHANNEL,DestinationAccessLevel.PRIVATE,5)
    def test_enums_are_lowercase(self):
        self.assertEqual([x.value for x in DestinationStatus],["active","inactive"])
        self.assertEqual([x.value for x in Exchange],["ourbit","toobit","lbank"])
    def test_creation_trims_and_starts_inactive(self):
        item=self.make(); self.assertEqual(item.internal_name,"Alpha"); self.assertIs(item.status,DestinationStatus.INACTIVE)
    def test_identity_and_daily_limit_validation(self):
        for chat in (0,2**63,-2**63-1,True):
            with self.assertRaises(InvalidDestinationInputError): Destination.create("x",chat,DestinationType.GROUP,DestinationAccessLevel.PUBLIC,1)
        for limit in (0,-1,True,2**63):
            with self.assertRaises(InvalidDestinationInputError): Destination.create("x",1,DestinationType.GROUP,DestinationAccessLevel.PUBLIC,limit)
    def test_rehydrate_requires_aware_timestamps(self):
        now=datetime.now(timezone.utc)
        item=Destination.rehydrate(id=1,internal_name="x",telegram_chat_id=1,destination_type=DestinationType.GROUP,access_level=DestinationAccessLevel.PUBLIC,status=DestinationStatus.ACTIVE,daily_limit=1,created_at=now,updated_at=now)
        self.assertEqual(item.created_at.tzinfo,timezone.utc)
        with self.assertRaises(InvalidDestinationInputError): Destination.rehydrate(id=1,internal_name="x",telegram_chat_id=1,destination_type=DestinationType.GROUP,access_level=DestinationAccessLevel.PUBLIC,status=DestinationStatus.ACTIVE,daily_limit=1,created_at=now.replace(tzinfo=None),updated_at=now)
    def test_metadata_preserves_chat_id(self):
        changed=self.make().with_metadata("Beta",DestinationType.GROUP,DestinationAccessLevel.PUBLIC,7)
        self.assertEqual(changed.telegram_chat_id,-100); self.assertEqual(changed.internal_name,"Beta")
    def test_status_transition_and_no_destructive_api(self):
        item=self.make(); self.assertIs(item.approve_transition(DestinationStatus.ACTIVE),DestinationStatus.ACTIVE)
        with self.assertRaises(InvalidDestinationStatusTransitionError): item.approve_transition(DestinationStatus.INACTIVE)
        for name in ("archive","delete","restore"): self.assertFalse(hasattr(item,name))
    def test_priority_validation_and_operations(self):
        p=DestinationExchangePriority((Exchange.OURBIT,)); p=p.enable(Exchange.LBANK)
        self.assertEqual(p.exchanges,(Exchange.OURBIT,Exchange.LBANK)); self.assertEqual(p.move(Exchange.LBANK,-1).exchanges,(Exchange.LBANK,Exchange.OURBIT))
        self.assertEqual(p.disable(Exchange.LBANK).exchanges,(Exchange.OURBIT,))
        for values in ((),(Exchange.OURBIT,Exchange.OURBIT),("ourbit",)):
            with self.assertRaises(InvalidExchangePriorityError): DestinationExchangePriority(values)
    def test_assignment_utc(self):
        a=DestinationSignalistAssignment(1,2,datetime.now(timezone.utc)); self.assertEqual(a.signalist_id,2)
