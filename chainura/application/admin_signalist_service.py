from chainura.domain.signalist import validate_telegram_user_id
from chainura.errors import InvalidSignalistInputError
from chainura.application.pagination import Page, PageRequest

class AdminSignalistService:
    def __init__(self, main_admin_id: int, signalists, transactions):
        self.main_admin_id=main_admin_id; self.signalists=signalists; self.transactions=transactions
    async def create(self,telegram_user_id:int,display_name:str,telegram_username:str|None=None):
        validate_telegram_user_id(telegram_user_id)
        if telegram_user_id == self.main_admin_id: raise InvalidSignalistInputError("main admin cannot be a signalist")
        if telegram_username is not None:
            telegram_username=telegram_username.strip()
            if telegram_username.startswith("@"): telegram_username=telegram_username[1:]
            telegram_username=telegram_username or None
        return await self.signalists.create(telegram_user_id,display_name,telegram_username)
    async def assignment_count(self,signalist_id:int)->int:
        async with self.transactions() as tx:
            tx.use_destination_errors()
            return await tx.destinations.count_for_signalist(signalist_id)
    async def assigned_destinations(self, signalist_id: int, request: PageRequest):
        async with self.transactions() as tx:
            tx.use_destination_errors()
            total = await tx.destinations.count_for_signalist(signalist_id)
            page = min(request.page, max(1, (total + request.page_size - 1) // request.page_size))
            items = await tx.destinations.list_for_signalist(
                signalist_id, (page - 1) * request.page_size, request.page_size
            )
            return Page(tuple(items), page, request.page_size, total)
