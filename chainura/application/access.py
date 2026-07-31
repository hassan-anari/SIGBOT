from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from chainura.application.signalist_service import SignalistService
from chainura.config import ApplicationSettings
from chainura.domain.signalist import Signalist, SignalistStatus, validate_telegram_user_id
from chainura.errors import ActorResolutionError, SignalistNotFoundError, SignalistPersistenceError


class ActorKind(str, Enum):
    MAIN_ADMIN = "main_admin"
    ACTIVE_SIGNALIST = "active_signalist"
    INACTIVE_SIGNALIST = "inactive_signalist"
    ARCHIVED_SIGNALIST = "archived_signalist"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Actor:
    kind: ActorKind
    signalist: Signalist | None = None

    @property
    def authorized(self) -> bool:
        return self.kind in {ActorKind.MAIN_ADMIN, ActorKind.ACTIVE_SIGNALIST}


class ActorResolver:
    def __init__(self, settings: ApplicationSettings, signalists: SignalistService) -> None:
        self._settings = settings
        self._signalists = signalists

    async def resolve(self, telegram_user_id: int) -> Actor:
        validate_telegram_user_id(telegram_user_id)
        if self._settings.is_main_admin(telegram_user_id):
            return Actor(ActorKind.MAIN_ADMIN)
        try:
            signalist = await self._signalists.get_by_telegram_user_id(telegram_user_id)
        except SignalistNotFoundError:
            return Actor(ActorKind.UNKNOWN)
        except SignalistPersistenceError as exc:
            raise ActorResolutionError("actor resolution failed") from exc
        kinds = {
            SignalistStatus.ACTIVE: ActorKind.ACTIVE_SIGNALIST,
            SignalistStatus.INACTIVE: ActorKind.INACTIVE_SIGNALIST,
            SignalistStatus.ARCHIVED: ActorKind.ARCHIVED_SIGNALIST,
        }
        return Actor(kinds[signalist.status], signalist)
