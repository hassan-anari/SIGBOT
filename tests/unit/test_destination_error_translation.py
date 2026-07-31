import unittest

from sqlalchemy.exc import IntegrityError

from chainura.errors import (DestinationPersistenceError, DuplicateDestinationAssignmentError,
    DuplicateDestinationChatIdError, DuplicateDestinationInternalNameError,
    InvalidExchangePriorityError)
from chainura.persistence.error_translation import destination_persistence_error


class DriverError(Exception): pass


class DestinationErrorTranslationTests(unittest.TestCase):
    def translate(self, message):
        return destination_persistence_error(IntegrityError("statement", {}, DriverError(message)))

    def test_known_sqlite_unique_signatures(self):
        cases = {
            "UNIQUE constraint failed: destinations.internal_name": DuplicateDestinationInternalNameError,
            "UNIQUE constraint failed: destinations.telegram_chat_id": DuplicateDestinationChatIdError,
            "UNIQUE constraint failed: destination_signalists.destination_id, destination_signalists.signalist_id": DuplicateDestinationAssignmentError,
            "UNIQUE constraint failed: destination_exchange_priorities.destination_id, destination_exchange_priorities.position": InvalidExchangePriorityError,
        }
        for message, expected in cases.items():
            self.assertIsInstance(self.translate(message), expected)

    def test_unknown_failure_is_sanitized(self):
        translated = destination_persistence_error(DriverError("secret connection details"))
        self.assertIsInstance(translated, DestinationPersistenceError)
        self.assertNotIn("secret", str(translated))
