from enum import Enum


class DestinationMessageKind(str, Enum):
    INITIAL_PHOTO_CAPTION = "initial_photo_caption"
    SIGNAL_DETAILS = "signal_details"
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    BREAKEVEN = "breakeven"
    SIGNAL_CANCELLED = "signal_cancelled"


class MessageTemplateSource(str, Enum):
    DESTINATION = "destination"
    GLOBAL = "global"
    BUILTIN = "builtin"
