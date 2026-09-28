from __future__ import annotations
from aiogram.filters.callback_data import CallbackData

ALLOWED_KINDS = frozenset({"ss","sp","out","ex","ns","np","dir","tp","ot","back","cancel",
                           "confirm","continue","confirm1","edit1","edit2"})

class SignalCallback(CallbackData, prefix="s5"):
    kind: str
    flow: str
    ref: str
    page: int

    def checked_pack(self) -> str:
        if self.kind not in ALLOWED_KINDS or not self.flow.isascii() or not self.ref.isascii():
            raise ValueError("invalid Phase 5 callback")
        value = self.pack()
        if len(value.encode("utf-8")) > 64:
            raise ValueError("Phase 5 callback exceeds Telegram limit")
        return value

def signal_callback(kind: str, flow: str, ref: str = "-", page: int = 0) -> str:
    return SignalCallback(kind=kind, flow=flow, ref=ref, page=page).checked_pack()
