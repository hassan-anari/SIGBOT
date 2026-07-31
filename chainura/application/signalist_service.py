from __future__ import annotations

from chainura.application.ports import SignalistTransactionFactory
from chainura.domain.signalist import Signalist, SignalistStatus
from chainura.errors import DuplicateTelegramUserIdError, SignalistNotFoundError


class SignalistService:
    def __init__(self, transaction_factory: SignalistTransactionFactory) -> None:
        self._transactions = transaction_factory

    async def create(
        self, telegram_user_id: int, display_name: str, telegram_username: str | None = None
    ) -> Signalist:
        candidate = Signalist.create(telegram_user_id, display_name, telegram_username)
        async with self._transactions() as transaction:
            if await transaction.repository.get_by_telegram_user_id(telegram_user_id):
                raise DuplicateTelegramUserIdError("telegram_user_id is already registered")
            result = await transaction.repository.add(candidate)
            await transaction.commit()
            return result

    async def get_by_id(self, signalist_id: int) -> Signalist:
        async with self._transactions() as transaction:
            result = await transaction.repository.get_by_id(signalist_id)
            if result is None:
                raise SignalistNotFoundError("signalist was not found")
            await transaction.commit()
            return result

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Signalist:
        async with self._transactions() as transaction:
            result = await transaction.repository.get_by_telegram_user_id(telegram_user_id)
            if result is None:
                raise SignalistNotFoundError("signalist was not found")
            await transaction.commit()
            return result

    async def list(self, status: SignalistStatus | None = None) -> list[Signalist]:
        async with self._transactions() as transaction:
            result = await transaction.repository.list(status)
            await transaction.commit()
            return result

    async def activate(self, signalist_id: int) -> Signalist:
        return await self._transition(signalist_id, SignalistStatus.ACTIVE)

    async def deactivate(self, signalist_id: int) -> Signalist:
        return await self._transition(signalist_id, SignalistStatus.INACTIVE)

    async def archive(self, signalist_id: int) -> Signalist:
        return await self._transition(signalist_id, SignalistStatus.ARCHIVED)

    async def _transition(self, signalist_id: int, target: SignalistStatus) -> Signalist:
        async with self._transactions() as transaction:
            current = await transaction.repository.get_by_id(signalist_id)
            if current is None:
                raise SignalistNotFoundError("signalist was not found")
            approved = current.approve_transition(target)
            result = await transaction.repository.persist_status(signalist_id, approved)
            await transaction.commit()
            return result
