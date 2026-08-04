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


class InvalidSignalInputError(ApplicationError):
    """A signal violates the approved Phase 5A domain rules."""


class EntryValidationError(ApplicationError):
    """Entry cannot be validated against a healthy current reference price."""


class ReferencePriceUnavailableError(EntryValidationError):
    """No healthy current reference price is available."""


class EntryOutsideToleranceError(EntryValidationError):
    """Entry exceeds the approved tolerance against the reference price."""


class InconsistentReferencePriceError(EntryValidationError):
    """A provider returned a malformed or context-inconsistent result."""


class SignalCreationError(ApplicationError):
    """Signal creation failed without exposing persistence/provider details."""


class SignalistCannotCreateSignalError(SignalCreationError):
    pass


class RendererNotConfiguredError(ApplicationError):
    pass

class RenderingError(ApplicationError): pass
class RenderingConfigurationError(RenderingError): pass
class RenderingLifecycleError(RenderingError): pass
class RenderPayloadError(RenderingError): pass
class TemplateError(RenderingError): pass
class TemplateValidationError(TemplateError): pass
class TemplatePersistenceError(TemplateError): pass
class TemplateNotConfiguredError(TemplateError): pass
class TemplateAssetError(TemplateError): pass
class UnsafeRenderPathError(RenderingError): pass
class PublicationError(ApplicationError): pass
class PublicationPlanningError(PublicationError): pass
class PublicationPersistenceError(PublicationError): pass
class DailyCapacityError(PublicationError): pass
class CaptionGenerationError(PublicationError): pass
class TelegramDeliveryError(PublicationError): pass
class PublicationLifecycleError(PublicationError): pass
class TemplateUploadTransportError(PublicationError): pass


class DuplicateSignalCreationTokenError(SignalCreationError):
    """The one known idempotency uniqueness constraint was raced."""


class SymbolCatalogError(ApplicationError):
    """A verified market symbol catalog is temporarily unavailable."""
