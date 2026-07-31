from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError

from chainura.domain.destination import DestinationType, MAX_SIGNED_BIGINT


class InspectionError(str, Enum):
    NOT_FOUND = "not_found"
    PRIVATE = "private"
    TEMPORARY = "temporary"


class DestinationInspectionError(Exception):
    def __init__(self, reason: InspectionError):
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class DestinationInspection:
    telegram_chat_id: int
    title: str
    destination_type: DestinationType
    bot_is_admin: bool


def normalize_chat_id(raw: str) -> int:
    value = raw.strip()
    digits = value[1:] if value.startswith("-") else value
    if (not value or value == "0" or not digits or not digits.isascii()
            or not digits.isdigit() or value.startswith("--")):
        raise ValueError("invalid chat id")
    if value.startswith("-"):
        normalized = int(value)
        if value != str(normalized):
            raise ValueError("chat id is not canonical")
    else:
        normalized = -int(value if value.startswith("100") else "100" + value)
    if normalized < -(MAX_SIGNED_BIGINT + 1) or normalized > MAX_SIGNED_BIGINT or normalized == 0:
        raise ValueError("chat id outside signed 64-bit range")
    return normalized


class TelegramDestinationInspector:
    def __init__(self, bot):
        self._bot = bot

    async def inspect(self, chat_id: int) -> DestinationInspection:
        try:
            me = await self._bot.get_me()
            chat = await self._bot.get_chat(chat_id)
            chat_type = getattr(chat.type, "value", chat.type)
            if chat_type == "private":
                raise DestinationInspectionError(InspectionError.PRIVATE)
            mapping = {"channel": DestinationType.CHANNEL, "group": DestinationType.GROUP,
                       "supergroup": DestinationType.GROUP}
            if chat_type not in mapping or not getattr(chat, "title", None):
                raise DestinationInspectionError(InspectionError.NOT_FOUND)
            member = await self._bot.get_chat_member(chat_id, me.id)
            status = getattr(member.status, "value", member.status)
            return DestinationInspection(chat_id, chat.title, mapping[chat_type],
                                         status in {"creator", "administrator"})
        except DestinationInspectionError:
            raise
        except TelegramBadRequest as exc:
            raise DestinationInspectionError(InspectionError.NOT_FOUND) from exc
        except (TelegramNetworkError, TelegramServerError) as exc:
            raise DestinationInspectionError(InspectionError.TEMPORARY) from exc
        except Exception as exc:
            raise DestinationInspectionError(InspectionError.TEMPORARY) from exc
