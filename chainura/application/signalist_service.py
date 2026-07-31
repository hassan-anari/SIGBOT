from __future__ import annotations

from chainura.application.ports import SignalistTransactionFactory
from chainura.application.pagination import Page, PageRequest
from chainura.domain.signalist import (
    Signalist,
    SignalistStatus,
    validate_internal_signalist_id,
    validate_telegram_user_id,
)
from chainura.errors import (
    DuplicateTelegramUserIdError,
    InvalidSignalistInputError,
    SignalistNotFoundError,
    SignalistPersistenceError,
)

MAX_STATUS_TRANSITION_ATTEMPTS = 3


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
        signalist_id = validate_internal_signalist_id(signalist_id)
        async with self._transactions() as transaction:
            result = await transaction.repository.get_by_id(signalist_id)
            if result is None:
                raise SignalistNotFoundError("signalist was not found")
            await transaction.commit()
            return result

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Signalist:
        telegram_user_id = validate_telegram_user_id(telegram_user_id)
        async with self._transactions() as transaction:
            result = await transaction.repository.get_by_telegram_user_id(telegram_user_id)
            if result is None:
                raise SignalistNotFoundError("signalist was not found")
            await transaction.commit()
            return result

    async def list(self, status: SignalistStatus | None = None) -> list[Signalist]:
        if status is not None and not isinstance(status, SignalistStatus):
            raise InvalidSignalistInputError("status must be a SignalistStatus or None")
        async with self._transactions() as transaction:
            result = await transaction.repository.list(status)
            await transaction.commit()
            return result

    async def list_page(self, status: SignalistStatus | None, request: PageRequest) -> Page[Signalist]:
        if status is not None and not isinstance(status, SignalistStatus):
            raise InvalidSignalistInputError("status must be a SignalistStatus or None")
        async with self._transactions() as transaction:
            total = await transaction.repository.count(status)
            page = min(request.page, max(1, (total + request.page_size - 1)//request.page_size))
            items = await transaction.repository.list_page(status, (page-1)*request.page_size, request.page_size)
            return Page(tuple(items), page, request.page_size, total)

    async def activate(self, signalist_id: int) -> Signalist:
        validate_internal_signalist_id(signalist_id)
        return await self._transition(signalist_id, SignalistStatus.ACTIVE)

    async def deactivate(self, signalist_id: int) -> Signalist:
        validate_internal_signalist_id(signalist_id)
        return await self._transition(signalist_id, SignalistStatus.INACTIVE)

    async def archive(self, signalist_id: int) -> Signalist:
        validate_internal_signalist_id(signalist_id)
        return await self._transition(signalist_id, SignalistStatus.ARCHIVED)

    async def _transition(self, signalist_id: int, target: SignalistStatus) -> Signalist:
        async with self._transactions() as transaction:
            current = await transaction.repository.get_by_id(signalist_id)
            if current is None:
                raise SignalistNotFoundError("signalist was not found")
            for _ in range(MAX_STATUS_TRANSITION_ATTEMPTS):
                approved = current.approve_transition(target)
                result = await transaction.repository.compare_and_swap_status(
                    signalist_id, current.status, approved
                )
                if result is not None:
                    await transaction.commit()
                    return result
                current = await transaction.repository.get_by_id_fresh(signalist_id)
                if current is None:
                    raise SignalistNotFoundError("signalist was not found")
                # Validate the latest committed state before retrying or exhausting.
                current.approve_transition(target)
            raise SignalistPersistenceError(
                "signalist status transition could not be completed"
            )
