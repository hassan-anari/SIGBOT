from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

class ApiOperation(str, Enum):
    PRICES="prices"; CATALOG="catalog"
class ServiceHealthStatus(str, Enum):
    UNKNOWN="unknown"; HEALTHY="healthy"; DEGRADED="degraded"; DOWN="down"
class AdminAlertKind(str, Enum):
    OUTAGE_INITIAL="outage_initial"; OUTAGE_REPEAT="outage_repeat"; RECOVERY="recovery"
class AdminAlertStatus(str, Enum):
    PENDING="pending"; SENDING="sending"; RETRY_WAIT="retry_wait"; SENT="sent"; FAILED="failed"; DELIVERY_UNKNOWN="delivery_unknown"; SUPERSEDED="superseded"
TERMINAL_ALERT_STATUSES=frozenset({AdminAlertStatus.SENT,AdminAlertStatus.FAILED,AdminAlertStatus.DELIVERY_UNKNOWN,AdminAlertStatus.SUPERSEDED})
REPEAT_SECONDS=60
ERROR_CATEGORIES=frozenset({"network","timeout","invalid_response","empty_response","stale_data","partial_data","unknown"})
def sanitize_error_category(value: str|None)->str|None:
    if value is None:return None
    key=str(value).strip().casefold().replace(" ","_")
    return key if key in ERROR_CATEGORIES else "unknown"
@dataclass(frozen=True,slots=True)
class HealthObservation:
    service_key:str; exchange:object; operation:ApiOperation; status:ServiceHealthStatus; observed_at:datetime; error_category:str|None=None
