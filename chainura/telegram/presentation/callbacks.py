from __future__ import annotations

from dataclasses import dataclass
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


FILTER_CODES = frozenset({"a", "v", "i", "r"})
EXCHANGE_CODES = frozenset({"o", "t", "l"})
FLOW_CODES = frozenset({"slc", "dsc", "dse", "dsp"})


def _positive(value: str) -> bool:
    return value.isascii() and value.isdigit() and value == str(int(value)) and int(value) > 0


def _negative_chat_id(value: str) -> bool:
    if not value.isascii() or not value.startswith("-") or not value[1:].isdigit():
        return False
    number = int(value)
    return value == str(number) and -(2**63) <= number < 0


def _valid(namespace: str, action: str, args: tuple[str, ...]) -> bool:
    if namespace == "sl":
        if action == "list": return len(args) == 2 and args[0] in FILTER_CODES and _positive(args[1])
        if action == "view": return len(args) == 1 and _positive(args[0])
        if action == "create": return not args
        if action == "status": return len(args) == 2 and _positive(args[0]) and args[1] in {"activate", "deactivate", "archive-request", "archive-confirm"}
        if action == "destinations": return len(args) == 2 and _positive(args[0]) and _positive(args[1])
        return False
    if namespace == "ds":
        if action == "list": return len(args) == 2 and args[0] in {"a", "v", "i"} and _positive(args[1])
        if action in {"view", "edit", "priority"}: return len(args) == 1 and _positive(args[0])
        if action == "create": return not args
        if action == "status": return len(args) == 2 and _positive(args[0]) and args[1] in {"activate", "deactivate"}
        return False
    if namespace == "de":
        if action == "access":
            return len(args) == 2 and _positive(args[0]) and args[1] in {"public", "private"}
        return False
    if namespace == "dp":
        if action in {"add", "remove"}: return len(args) == 2 and _positive(args[0]) and args[1] in EXCHANGE_CODES
        if action == "move": return len(args) == 3 and _positive(args[0]) and args[1] in EXCHANGE_CODES and args[2] in {"u", "d"}
        if action in {"review", "confirm", "back"}: return len(args) == 1 and _positive(args[0])
        return False
    if namespace == "as":
        if action == "list": return len(args) == 3 and _positive(args[0]) and args[1] in {"assigned", "eligible"} and _positive(args[2])
        if action in {"add", "remove"}: return len(args) == 2 and all(_positive(item) for item in args)
        if action == "confirm": return len(args) == 3 and args[0] == "remove" and all(_positive(item) for item in args[1:])
        return False
    if namespace == "rp":
        if action in {"scope","period","summary","hist","sig","sigsel"}: return all(arg.isascii() and len(arg) <= 16 for arg in args)
        return False
    if namespace == "nav":
        if action in {"candidate-confirm", "candidate-retry"}: return len(args) == 1 and _negative_chat_id(args[0])
        if action == "access": return len(args) == 1 and args[0] in {"public", "private"}
        if action == "exchange": return len(args) == 2 and args[0] in {"add", "remove", "up", "down"} and args[1] in EXCHANGE_CODES
        if action == "confirm": return len(args) == 1 and args[0] in FLOW_CODES
        if action == "cancel": return len(args) == 1 and args[0] in FLOW_CODES
        if action == "back": return len(args) == 2 and args[0] in FLOW_CODES and args[1].isascii() and args[1].isalpha()
        return False
    return False


@dataclass(frozen=True, slots=True)
class Phase4Callback:
    namespace: str
    action: str
    arguments: tuple[str, ...] = ()

    def encode(self) -> str:
        value = ":".join(("p4", self.namespace, self.action, *self.arguments))
        if not value.isascii() or len(value.encode("utf-8")) > 64 or not _valid(
            self.namespace, self.action, self.arguments
        ):
            raise ValueError("invalid Phase 4 callback")
        return value


def parse_phase4(value: str | None) -> Phase4Callback | None:
    if not value or not value.isascii() or len(value.encode("utf-8")) > 64:
        return None
    parts = value.split(":")
    if len(parts) < 3 or parts[0] != "p4":
        return None
    callback = Phase4Callback(parts[1], parts[2], tuple(parts[3:]))
    return callback if _valid(callback.namespace, callback.action, callback.arguments) else None
