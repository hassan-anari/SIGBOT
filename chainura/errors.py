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
