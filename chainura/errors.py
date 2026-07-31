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


class InvalidDestinationInputError(ApplicationError): pass
class DestinationNotFoundError(ApplicationError): pass
class DuplicateDestinationChatIdError(ApplicationError): pass
class DuplicateDestinationInternalNameError(ApplicationError): pass
class InvalidDestinationStatusTransitionError(ApplicationError): pass
class InvalidExchangePriorityError(ApplicationError): pass
class DestinationPersistenceError(ApplicationError): pass
class SignalistNotEligibleForAssignmentError(ApplicationError): pass
class DuplicateDestinationAssignmentError(ApplicationError): pass
class DestinationAssignmentNotFoundError(ApplicationError): pass


class DatabaseInitializationError(ApplicationError):
    """SQLite could not be initialized with the required safety settings."""


class ActorResolutionError(ApplicationError):
    """A sanitized failure while resolving the current Telegram actor."""


class TelegramRuntimeError(ApplicationError):
    """A sanitized Telegram runtime or coordination failure."""
