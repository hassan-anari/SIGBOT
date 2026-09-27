from enum import Enum


class DestinationMessageKind(str, Enum):
    INITIAL_PHOTO_CAPTION = "initial_photo_caption"
    SIGNAL_DETAILS = "signal_details"
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    BREAKEVEN = "breakeven"
    SIGNAL_CANCELLED = "signal_cancelled"
    WARMUP = "warmup"
    RISK_FREE_ACTIVATED = "risk_free_activated"
    SAVE_PROFIT_50 = "save_profit_50"
    FULL_CLOSE = "full_close"
    RISK_FREE_HIT = "risk_free_hit"


class MessageTemplateSource(str, Enum):
    DESTINATION = "destination"
    GLOBAL = "global"
    BUILTIN = "builtin"
