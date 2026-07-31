import unittest
from unittest.mock import AsyncMock

from sqlalchemy.exc import SQLAlchemyError

from chainura.errors import DestinationPersistenceError
from chainura.persistence.destination_repository import SqlAlchemyDestinationRepository


class FailingSession:
    def __init__(self):
        error=SQLAlchemyError("driver connection details must remain hidden")
        self.scalar=AsyncMock(side_effect=error)
        self.scalars=AsyncMock(side_effect=error)
        self.get=AsyncMock(side_effect=error)
        self.execute=AsyncMock(side_effect=error)


class DestinationRepositoryFailureTests(unittest.IsolatedAsyncioTestCase):
    async def assert_sanitized(self, awaitable):
        with self.assertRaises(DestinationPersistenceError) as caught:
            await awaitable
        self.assertNotIn("driver", str(caught.exception))
        self.assertNotIn("connection", str(caught.exception))

    async def test_read_and_count_failures_are_sanitized(self):
        repository=SqlAlchemyDestinationRepository(FailingSession())  # type: ignore[arg-type]
        operations=(repository.count(),repository.list_page(None,0,8),repository.get_priority(1),
                    repository.assignment_exists(1,1),repository.count_assigned(1),
                    repository.list_assigned(1,0,8),repository.count_eligible(1),
                    repository.list_eligible(1,0,8),repository.count_for_signalist(1),
                    repository.list_for_signalist(1,0,8))
        for operation in operations:
            await self.assert_sanitized(operation)

    async def test_delete_failure_is_sanitized(self):
        repository=SqlAlchemyDestinationRepository(FailingSession())  # type: ignore[arg-type]
        await self.assert_sanitized(repository.remove_assignment(1,1))
