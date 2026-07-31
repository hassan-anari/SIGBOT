from sqlalchemy.exc import IntegrityError

from chainura.errors import (DestinationPersistenceError, DuplicateDestinationAssignmentError,
    DuplicateDestinationChatIdError, DuplicateDestinationInternalNameError,
    DuplicateTelegramUserIdError, InvalidExchangePriorityError, SignalistPersistenceError)

DUPLICATE_TELEGRAM_CONSTRAINT = "uq_signalists_telegram_user_id"


def translate_integrity_error(exc: IntegrityError) -> Exception:
    diagnostic = getattr(exc.orig, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    sqlite_duplicate = "UNIQUE constraint failed: signalists.telegram_user_id"
    if constraint == DUPLICATE_TELEGRAM_CONSTRAINT or sqlite_duplicate in str(exc.orig):
        return DuplicateTelegramUserIdError("telegram_user_id is already registered")
    raw = str(exc.orig)
    if constraint == "uq_destinations_internal_name" or "destinations.internal_name" in raw:
        return DuplicateDestinationInternalNameError("internal_name is already registered")
    if constraint == "uq_destinations_telegram_chat_id" or "destinations.telegram_chat_id" in raw:
        return DuplicateDestinationChatIdError("telegram_chat_id is already registered")
    if constraint == "pk_destination_signalists" or (
        "destination_signalists.destination_id" in raw and "destination_signalists.signalist_id" in raw
    ):
        return DuplicateDestinationAssignmentError("assignment already exists")
    if constraint in {"pk_destination_exchange_priorities", "uq_destination_exchange_priorities_destination_position"} or (
        "destination_exchange_priorities.destination_id" in raw and
        ("destination_exchange_priorities.exchange" in raw or "destination_exchange_priorities.position" in raw)
    ):
        return InvalidExchangePriorityError("priority violates a uniqueness constraint")
    return SignalistPersistenceError("signalist persistence operation failed")


def persistence_error(exc: Exception) -> Exception:
    if isinstance(exc, IntegrityError):
        return translate_integrity_error(exc)
    return SignalistPersistenceError("signalist persistence operation failed")


def destination_persistence_error(exc: Exception) -> Exception:
    if isinstance(exc, IntegrityError):
        translated = translate_integrity_error(exc)
        if not isinstance(translated, SignalistPersistenceError):
            return translated
    return DestinationPersistenceError("destination persistence operation failed")
