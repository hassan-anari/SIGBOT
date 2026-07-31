class ApplicationError(Exception):
    """Base class for controlled application errors."""


class InvalidApplicationConfigurationError(ApplicationError):
    pass


class InvalidSignalistInputError(ApplicationError):
    pass


class InvalidSignalistStatusTransitionError(ApplicationError):
    pass


class SignalistNotFoundError(ApplicationError):
    pass


class DuplicateTelegramUserIdError(ApplicationError):
    pass


class SignalistPersistenceError(ApplicationError):
    pass


class ActorResolutionError(ApplicationError):
    """A sanitized failure while resolving the current Telegram actor."""


class TelegramRuntimeError(ApplicationError):
    """A sanitized Telegram runtime or coordination failure."""
