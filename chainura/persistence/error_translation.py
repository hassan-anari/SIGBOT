from sqlalchemy.exc import IntegrityError

from chainura.errors import DuplicateTelegramUserIdError, SignalistPersistenceError

DUPLICATE_TELEGRAM_CONSTRAINT = "uq_signalists_telegram_user_id"


def translate_integrity_error(exc: IntegrityError) -> Exception:
    diagnostic = getattr(exc.orig, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    if constraint == DUPLICATE_TELEGRAM_CONSTRAINT:
        return DuplicateTelegramUserIdError("telegram_user_id is already registered")
    return SignalistPersistenceError("signalist persistence operation failed")


def persistence_error(exc: Exception) -> Exception:
    if isinstance(exc, IntegrityError):
        return translate_integrity_error(exc)
    return SignalistPersistenceError("signalist persistence operation failed")
