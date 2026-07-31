from datetime import datetime, timezone
from chainura.application.pagination import Page, PageRequest
from chainura.domain.signalist import SignalistStatus
from chainura.errors import (DestinationAssignmentNotFoundError, DestinationNotFoundError,
    DuplicateDestinationAssignmentError, SignalistNotEligibleForAssignmentError,
    SignalistNotFoundError)

class DestinationAssignmentService:
    def __init__(self,transactions): self._transactions=transactions
    async def list_assigned(self,did:int,request:PageRequest):
        async with self._transactions() as tx:
            tx.use_destination_errors()
            if await tx.destinations.get_by_id(did) is None: raise DestinationNotFoundError("destination not found")
            total=await tx.destinations.count_assigned(did); page=min(request.page,max(1,(total+7)//8))
            return Page(tuple(await tx.destinations.list_assigned(did,(page-1)*8,8)),page,8,total)
    async def list_eligible(self,did:int,request:PageRequest):
        async with self._transactions() as tx:
            tx.use_destination_errors()
            if await tx.destinations.get_by_id(did) is None: raise DestinationNotFoundError("destination not found")
            total=await tx.destinations.count_eligible(did); page=min(request.page,max(1,(total+7)//8))
            return Page(tuple(await tx.destinations.list_eligible(did,(page-1)*8,8)),page,8,total)
    async def assign(self,did:int,sid:int):
        async with self._transactions() as tx:
            tx.use_destination_errors()
            if await tx.destinations.get_by_id(did,True) is None: raise DestinationNotFoundError("destination not found")
            signalist=await tx.signalists.get_by_id_fresh(sid)
            if signalist is None: raise SignalistNotFoundError("signalist not found")
            if signalist.status is SignalistStatus.ARCHIVED: raise SignalistNotEligibleForAssignmentError("signalist is archived")
            if await tx.destinations.assignment_exists(did,sid): raise DuplicateDestinationAssignmentError("assignment exists")
            result=await tx.destinations.add_assignment(did,sid); await tx.commit(); return result
    async def unassign(self,did:int,sid:int)->None:
        async with self._transactions() as tx:
            tx.use_destination_errors()
            if await tx.destinations.get_by_id(did,True) is None: raise DestinationNotFoundError("destination not found")
            if not await tx.destinations.remove_assignment(did,sid): raise DestinationAssignmentNotFoundError("assignment missing")
            await tx.commit()
