from enum import Enum


class CallbackAction(str, Enum):
    MENU = "p3:menu"
    STATUS = "p3:status"
    REFRESH_STATUS = "p3:refresh:status"


def parse_callback(value: str | None) -> CallbackAction | None:
    if value is None or len(value.encode("utf-8")) > 64:
        return None
    try:
        return CallbackAction(value)
    except ValueError:
        return None
