from enum import Enum
from dataclasses import dataclass


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

FILTER_CODES = {"a", "v", "i", "r"}
EXCHANGE_CODES = {"o", "t", "l"}

@dataclass(frozen=True, slots=True)
class Phase4Callback:
    namespace: str
    action: str
    arguments: tuple[str, ...] = ()
    def encode(self) -> str:
        value=":".join(("p4",self.namespace,self.action,*self.arguments))
        if not value.isascii() or len(value.encode())>64: raise ValueError("invalid callback length")
        return value

def _positive(value: str) -> bool:
    return value.isascii() and value.isdigit() and value == str(int(value)) and int(value)>0

def parse_phase4(value: str | None) -> Phase4Callback | None:
    if not value or not value.isascii() or len(value.encode())>64: return None
    parts=value.split(":")
    if len(parts)<3 or parts[0]!="p4" or parts[1] not in {"sl","ds","dp","as","nav"}: return None
    ns,action,args=parts[1],parts[2],tuple(parts[3:])
    valid=False
    if ns in {"sl","ds"}:
        valid=(action=="list" and len(args)==2 and args[0] in FILTER_CODES and _positive(args[1])) or \
              (action in {"view","create"} and ((action=="view" and len(args)==1 and _positive(args[0])) or (action=="create" and not args))) or \
              (action=="status" and len(args) in {2,3} and _positive(args[0]) and args[1] in {"activate","deactivate","archive"} and (len(args)==2 or args[2]=="confirm")) or \
              (ns=="ds" and action in {"edit","priority"} and len(args)==1 and _positive(args[0]))
    elif ns=="as":
        valid=(action=="list" and len(args)==3 and _positive(args[0]) and args[1] in {"assigned","eligible"} and _positive(args[2])) or \
              (action in {"add","remove"} and len(args)==2 and all(_positive(x) for x in args)) or \
              (action=="confirm" and len(args)==3 and args[0]=="remove" and all(_positive(x) for x in args[1:]))
    elif ns=="dp":
        valid=(action in {"add","remove"} and len(args)==2 and _positive(args[0]) and args[1] in EXCHANGE_CODES) or \
              (action=="move" and len(args)==3 and _positive(args[0]) and args[1] in EXCHANGE_CODES and args[2] in {"u","d"}) or \
              (action in {"review","confirm"} and len(args)==1 and _positive(args[0]))
    elif ns=="nav": valid=action in {"cancel","back","confirm","type","access","exchange"} and 1<=len(args)<=2 and all(x.isascii() and x for x in args)
    return Phase4Callback(ns,action,args) if valid else None
