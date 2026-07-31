from __future__ import annotations
from dataclasses import dataclass
from chainura.application.pagination import Page, PageRequest
from chainura.domain.destination import (Destination, DestinationAccessLevel,
    DestinationExchangePriority, DestinationStatus, DestinationType, Exchange)
from chainura.errors import (DestinationNotFoundError, DestinationPersistenceError,
    InvalidDestinationInputError)

@dataclass(frozen=True, slots=True)
class DestinationDetail:
    destination: Destination
    priority: DestinationExchangePriority
    assigned_count: int

class DestinationService:
    def __init__(self, transactions): self._transactions=transactions
    async def create(self,name,chat_id,dtype,access,daily_limit,exchanges)->DestinationDetail:
        candidate=Destination.create(name,chat_id,dtype,access,daily_limit)
        priority=DestinationExchangePriority(tuple(exchanges))
        async with self._transactions() as tx:
            item=await tx.destinations.add(candidate)
            await tx.destinations.replace_priority(item.id,priority)
            await tx.commit()
            return DestinationDetail(item,priority,0)
    async def get(self,id:int)->Destination:
        if isinstance(id,bool) or not isinstance(id,int) or id<=0: raise InvalidDestinationInputError("invalid destination id")
        async with self._transactions() as tx:
            item=await tx.destinations.get_by_id(id)
            if item is None: raise DestinationNotFoundError("destination not found")
            return item
    async def detail(self,id:int)->DestinationDetail:
        async with self._transactions() as tx:
            item=await tx.destinations.get_by_id(id)
            if item is None: raise DestinationNotFoundError("destination not found")
            return DestinationDetail(item,await tx.destinations.get_priority(id),await tx.destinations.count_assigned(id))
    async def list_page(self,status:DestinationStatus|None,request:PageRequest)->Page[Destination]:
        if status is not None and not isinstance(status,DestinationStatus): raise InvalidDestinationInputError("invalid status")
        async with self._transactions() as tx:
            total=await tx.destinations.count(status); page=min(request.page,max(1,(total+7)//8)); offset=(page-1)*8
            items=await tx.destinations.list_page(status,offset,8)
            return Page(tuple(items),page,8,total)
    async def update_metadata(self,id:int,name:str,dtype:DestinationType,access:DestinationAccessLevel,
                              daily_limit:int,expected_updated_at)->Destination:
        async with self._transactions() as tx:
            current=await tx.destinations.get_by_id(id)
            if current is None: raise DestinationNotFoundError("destination not found")
            if current.updated_at != expected_updated_at: raise DestinationPersistenceError("destination changed")
            candidate=current.with_metadata(name,dtype,access,daily_limit)
            result=await tx.destinations.update_metadata(current,candidate)
            if result is None: raise DestinationPersistenceError("destination changed")
            await tx.commit(); return result
    async def activate(self,id:int)->Destination: return await self._transition(id,DestinationStatus.ACTIVE)
    async def deactivate(self,id:int)->Destination: return await self._transition(id,DestinationStatus.INACTIVE)
    async def _transition(self,id:int,target:DestinationStatus)->Destination:
        async with self._transactions() as tx:
            current=await tx.destinations.get_by_id(id)
            if current is None: raise DestinationNotFoundError("destination not found")
            current.approve_transition(target)
            result=await tx.destinations.compare_and_swap_status(id,current.status,target)
            if result is None:
                latest=await tx.destinations.get_by_id(id,True)
                if latest is None: raise DestinationNotFoundError("destination not found")
                latest.approve_transition(target)
                raise DestinationPersistenceError("destination changed")
            await tx.commit(); return result
    async def replace_priority(self,id:int,exchanges,expected_updated_at)->DestinationDetail:
        priority=DestinationExchangePriority(tuple(exchanges))
        async with self._transactions() as tx:
            current=await tx.destinations.get_by_id(id,True)
            if current is None: raise DestinationNotFoundError("destination not found")
            if current.updated_at != expected_updated_at: raise DestinationPersistenceError("destination changed")
            if not await tx.destinations.touch(id, expected_updated_at):
                raise DestinationPersistenceError("destination changed")
            await tx.destinations.replace_priority(id,priority)
            assigned=await tx.destinations.count_assigned(id)
            updated=await tx.destinations.get_by_id(id,True)
            await tx.commit()
            return DestinationDetail(updated,priority,assigned)
