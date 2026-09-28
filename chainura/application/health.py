from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from chainura.domain.destination import Exchange
from chainura.domain.health import (
    AdminAlertKind,
    AdminAlertStatus,
    ApiOperation,
    HealthObservation,
    REPEAT_SECONDS,
    ServiceHealthStatus,
    TERMINAL_ALERT_STATUSES,
    sanitize_error_category,
)
from chainura.persistence.models import AdminAlertRecord, OutageIncidentRecord, ServiceHealthRecord

LOGGER = logging.getLogger("chainura.health")
TEHRAN = ZoneInfo("Asia/Tehran")
SERVICE_LABELS = {
    "ourbit_prices": "قیمت Ourbit",
    "ourbit_catalog": "فهرست قراردادهای Ourbit",
    "toobit_prices": "قیمت Toobit",
    "toobit_catalog": "فهرست قراردادهای Toobit",
    "lbank_prices": "قیمت LBank",
    "lbank_catalog": "فهرست قراردادهای LBank",
    "weex_prices": "قیمت WEEX",
    "weex_catalog": "فهرست قراردادهای WEEX",
}
ALL_SERVICES: tuple[tuple[str, Exchange, ApiOperation], ...] = tuple(
    (f"{exchange.value}_{operation.value}", exchange, operation)
    for exchange in Exchange
    for operation in (ApiOperation.PRICES, ApiOperation.CATALOG)
)


def service_key(exchange: Exchange, operation: ApiOperation) -> str:
    return f"{exchange.value}_{operation.value}"


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("health observation time must be aware")
    return value.astimezone(timezone.utc)


class HealthRepository:
    def __init__(self, sessions: async_sessionmaker, wake_alerts: Callable[[], None] | None = None):
        self.sessions = sessions
        self.wake_alerts = wake_alerts

    async def seed_unknown_services(self, now: datetime) -> None:
        now = _aware_utc(now)
        async with self.sessions() as session, session.begin():
            for key, exchange, operation in ALL_SERVICES:
                if await session.get(ServiceHealthRecord, key) is None:
                    session.add(ServiceHealthRecord(
                        service_key=key, exchange=exchange, operation=operation,
                        status=ServiceHealthStatus.UNKNOWN, consecutive_successes=0,
                        consecutive_failures=0, created_at=now, updated_at=now,
                    ))

    async def observe(self, obs: HealthObservation) -> bool:
        """Persist one observation. Return True when alert outbox work was created/superseded."""
        now = _aware_utc(obs.observed_at)
        cat = sanitize_error_category(obs.error_category)
        created_alert = False
        async with self.sessions() as session, session.begin():
            row = await session.get(ServiceHealthRecord, obs.service_key)
            if row is None:
                row = ServiceHealthRecord(
                    service_key=obs.service_key, exchange=obs.exchange, operation=obs.operation,
                    status=ServiceHealthStatus.UNKNOWN, consecutive_successes=0,
                    consecutive_failures=0, created_at=now, updated_at=now,
                )
                session.add(row)
                await session.flush()
            was_down = row.status is ServiceHealthStatus.DOWN and row.current_incident_id is not None
            row.last_attempt_at = now
            row.updated_at = now
            row.last_error_category = cat
            if obs.status is ServiceHealthStatus.DOWN:
                row.last_failure_at = now
                row.consecutive_failures += 1
                row.consecutive_successes = 0
                incident = await session.get(OutageIncidentRecord, row.current_incident_id) if row.current_incident_id else None
                if incident is None:
                    incident = OutageIncidentRecord(
                        service_key=obs.service_key, exchange=obs.exchange, operation=obs.operation,
                        started_at=now, initial_error_category=cat, latest_error_category=cat,
                        created_at=now, updated_at=now,
                    )
                    session.add(incident)
                    await session.flush()
                    row.current_incident_id = incident.id
                    row.unhealthy_since = now
                    row.next_repeat_at = now + timedelta(seconds=REPEAT_SECONDS)
                    session.add(AdminAlertRecord(
                        incident_id=incident.id, service_key=obs.service_key,
                        kind=AdminAlertKind.OUTAGE_INITIAL, repeat_sequence=0,
                        status=AdminAlertStatus.PENDING, attempts=0, sanitized_error=cat,
                        created_at=now, updated_at=now,
                    ))
                    created_alert = True
                else:
                    incident.latest_error_category = cat
                    incident.updated_at = now
                row.status = ServiceHealthStatus.DOWN
                if created_alert and self.wake_alerts is not None:
                    self.wake_alerts()
                return created_alert
            row.last_success_at = now
            row.consecutive_successes += 1
            row.consecutive_failures = 0
            if was_down:
                incident = await session.get(OutageIncidentRecord, row.current_incident_id)
                if incident and incident.recovered_at is None:
                    incident.recovered_at = now
                    incident.updated_at = now
                    incident.latest_error_category = cat
                    await session.execute(update(AdminAlertRecord).where(
                        AdminAlertRecord.incident_id == incident.id,
                        AdminAlertRecord.kind == AdminAlertKind.OUTAGE_REPEAT,
                        AdminAlertRecord.status.in_((AdminAlertStatus.PENDING, AdminAlertStatus.RETRY_WAIT)),
                    ).values(status=AdminAlertStatus.SUPERSEDED, updated_at=now))
                    session.add(AdminAlertRecord(
                        incident_id=incident.id, service_key=obs.service_key,
                        kind=AdminAlertKind.RECOVERY, repeat_sequence=0,
                        status=AdminAlertStatus.PENDING, attempts=0, sanitized_error=cat,
                        created_at=now, updated_at=now,
                    ))
                    created_alert = True
                row.current_incident_id = None
                row.unhealthy_since = None
                row.next_repeat_at = None
            row.status = obs.status
        if created_alert and self.wake_alerts:
            self.wake_alerts()
        return created_alert

    async def enqueue_due_repeats(self, now: datetime) -> int:
        now = _aware_utc(now)
        count = 0
        async with self.sessions() as session, session.begin():
            rows = (await session.scalars(select(ServiceHealthRecord).where(
                ServiceHealthRecord.status == ServiceHealthStatus.DOWN,
                ServiceHealthRecord.current_incident_id.is_not(None),
                ServiceHealthRecord.next_repeat_at <= now,
            ))).all()
            for row in rows:
                incident = await session.get(OutageIncidentRecord, row.current_incident_id)
                if incident is None or incident.recovered_at is not None:
                    row.current_incident_id = None
                    row.next_repeat_at = None
                    row.unhealthy_since = None
                    row.status = ServiceHealthStatus.UNKNOWN
                    row.updated_at = now
                    continue
                seq = (await session.scalar(select(AdminAlertRecord.repeat_sequence).where(
                    AdminAlertRecord.incident_id == incident.id,
                    AdminAlertRecord.kind == AdminAlertKind.OUTAGE_REPEAT,
                ).order_by(AdminAlertRecord.repeat_sequence.desc()).limit(1))) or 0
                session.add(AdminAlertRecord(
                    incident_id=incident.id, service_key=row.service_key,
                    kind=AdminAlertKind.OUTAGE_REPEAT, repeat_sequence=seq + 1,
                    status=AdminAlertStatus.PENDING, attempts=0,
                    sanitized_error=row.last_error_category, created_at=now, updated_at=now,
                ))
                row.next_repeat_at = now + timedelta(seconds=REPEAT_SECONDS)
                row.updated_at = now
                count += 1
        return count

    async def list_health(self):
        async with self.sessions() as session:
            return tuple((await session.scalars(select(ServiceHealthRecord).order_by(ServiceHealthRecord.service_key))).all())

    async def recover_sending(self, now: datetime) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(update(AdminAlertRecord).where(
                AdminAlertRecord.status == AdminAlertStatus.SENDING
            ).values(status=AdminAlertStatus.DELIVERY_UNKNOWN, updated_at=_aware_utc(now)))

    async def due_alerts(self, now: datetime, limit: int):
        now = _aware_utc(now)
        async with self.sessions() as session:
            return tuple((await session.scalars(select(AdminAlertRecord).where(
                AdminAlertRecord.status.in_((AdminAlertStatus.PENDING, AdminAlertStatus.RETRY_WAIT)),
                (AdminAlertRecord.retry_at.is_(None)) | (AdminAlertRecord.retry_at <= now),
            ).order_by(AdminAlertRecord.created_at, AdminAlertRecord.id).limit(limit))).all())

    async def mark_sending(self, alert_id: int, now: datetime):
        now = _aware_utc(now)
        async with self.sessions() as session, session.begin():
            row = await session.get(AdminAlertRecord, alert_id)
            if row is None or row.status in TERMINAL_ALERT_STATUSES:
                return None
            if row.kind is AdminAlertKind.OUTAGE_REPEAT:
                incident = await session.get(OutageIncidentRecord, row.incident_id)
                if incident is None or incident.recovered_at is not None:
                    row.status = AdminAlertStatus.SUPERSEDED
                    row.updated_at = now
                    return None
            row.status = AdminAlertStatus.SENDING
            row.attempts += 1
            row.updated_at = now
            return row

    async def mark_sent(self, alert_id: int, message_id: int, now: datetime) -> None:
        now = _aware_utc(now)
        async with self.sessions() as session, session.begin():
            row = await session.get(AdminAlertRecord, alert_id)
            if row is None or row.status in TERMINAL_ALERT_STATUSES:
                return
            row.status = AdminAlertStatus.SENT
            row.telegram_message_id = message_id
            row.sent_at = now
            row.updated_at = now

    async def mark_unknown_fresh(self, alert_id: int, now: datetime) -> None:
        now = _aware_utc(now)
        async with self.sessions() as session, session.begin():
            row = await session.get(AdminAlertRecord, alert_id)
            if row is None or row.status in {AdminAlertStatus.SENT, AdminAlertStatus.DELIVERY_UNKNOWN}:
                return
            row.status = AdminAlertStatus.DELIVERY_UNKNOWN
            row.telegram_message_id = None
            row.sent_at = None
            row.updated_at = now

    async def mark_retry_or_failed(self, alert_id: int, now: datetime, max_attempts: int, base: float, retry_after: float | None = None, definite: bool = False) -> None:
        now = _aware_utc(now)
        async with self.sessions() as session, session.begin():
            row = await session.get(AdminAlertRecord, alert_id)
            if row is None or row.status in TERMINAL_ALERT_STATUSES:
                return
            if definite or row.attempts >= max_attempts:
                row.status = AdminAlertStatus.FAILED
                row.retry_at = None
            else:
                delay = retry_after if retry_after is not None else base * (2 ** max(0, row.attempts - 1))
                row.status = AdminAlertStatus.RETRY_WAIT
                row.retry_at = now + timedelta(seconds=delay)
            row.updated_at = now


class HealthObservationRecorder:
    def __init__(self, repository: HealthRepository | None, clock: Callable[[], datetime], wake_alerts: Callable[[], None] | None = None):
        self.repository = repository
        self.clock = clock
        self.wake_alerts = wake_alerts

    async def record(self, observation: HealthObservation) -> None:
        if self.repository is None:
            return
        try:
            created = await self.repository.observe(observation)
        except Exception as exc:
            LOGGER.warning("health observation persistence failed service=%s category=%s", observation.service_key, type(exc).__name__)
            return
        if created and self.wake_alerts:
            self.wake_alerts()


def price_observation(exchange: Exchange, ticks: Iterable[object] | BaseException, expected_count: int, observed_at: datetime) -> HealthObservation:
    if isinstance(ticks, BaseException):
        return HealthObservation(service_key(exchange, ApiOperation.PRICES), exchange, ApiOperation.PRICES, ServiceHealthStatus.DOWN, observed_at, "network")
    valid = tuple(x for x in ticks if isinstance(getattr(x, "last_price", None), Decimal) and x.last_price.is_finite() and x.last_price > 0)
    if not valid:
        status = ServiceHealthStatus.DOWN
        category = "empty_response"
    elif expected_count and len(valid) < expected_count:
        status = ServiceHealthStatus.DEGRADED
        category = "partial_data"
    else:
        status = ServiceHealthStatus.HEALTHY
        category = None
    return HealthObservation(service_key(exchange, ApiOperation.PRICES), exchange, ApiOperation.PRICES, status, observed_at, category)


def catalog_observation(exchange: Exchange, rows: Iterable[object] | BaseException, observed_at: datetime) -> HealthObservation:
    if isinstance(rows, BaseException):
        return HealthObservation(service_key(exchange, ApiOperation.CATALOG), exchange, ApiOperation.CATALOG, ServiceHealthStatus.DOWN, observed_at, "network")
    usable = tuple(x for x in rows if getattr(x, "canonical_symbol", None) and getattr(x, "exchange_symbol", None))
    status = ServiceHealthStatus.HEALTHY if usable else ServiceHealthStatus.DOWN
    return HealthObservation(service_key(exchange, ApiOperation.CATALOG), exchange, ApiOperation.CATALOG, status, observed_at, None if usable else "empty_response")


async def persist_observations_safely(recorder: HealthObservationRecorder | None, observations: Iterable[HealthObservation]) -> None:
    if recorder is None:
        return
    for observation in observations:
        await recorder.record(observation)


class AdminAlertWorker:
    def __init__(self, repo: HealthRepository, transport, admin_id: int, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), concurrency: int = 1, max_attempts: int = 3, retry_base_seconds: float = .5, sweep_seconds: float = 5):
        self.repo = repo
        self.transport = transport
        self.admin_id = admin_id
        self.clock = clock
        self.concurrency = concurrency
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.sweep_seconds = sweep_seconds
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self.task = None

    def wake(self):
        self._wake.set()

    async def start(self):
        await self.repo.recover_sending(self.clock())
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        while not self._stop.is_set():
            try:
                if await self.repo.enqueue_due_repeats(self.clock()):
                    self.wake()
                alerts = await self.repo.due_alerts(self.clock(), self.concurrency)
                await asyncio.gather(*(self._send(alert.id) for alert in alerts))
            except Exception as exc:
                LOGGER.warning("admin alert worker sweep failed category=%s", type(exc).__name__)
            try:
                await asyncio.wait_for(self._wake.wait(), self.sweep_seconds)
                self._wake.clear()
            except TimeoutError:
                pass

    async def _send(self, alert_id: int):
        row = await self.repo.mark_sending(alert_id, self.clock())
        if row is None:
            return
        try:
            msg = await self.transport.send_message(self.admin_id, await admin_alert_text_from_repo(self.repo, row.id))
        except TimeoutError:
            await self.repo.mark_unknown_fresh(alert_id, self.clock())
        except Exception as exc:
            name = type(exc).__name__.casefold()
            retry_after = getattr(exc, "retry_after", None)
            if retry_after is not None or "retry" in name:
                await self.repo.mark_retry_or_failed(alert_id, self.clock(), self.max_attempts, self.retry_base_seconds, retry_after=float(retry_after or self.retry_base_seconds))
            elif "forbidden" in name or "badrequest" in name or "bad_request" in name:
                await self.repo.mark_retry_or_failed(alert_id, self.clock(), self.max_attempts, self.retry_base_seconds, definite=True)
            else:
                await self.repo.mark_unknown_fresh(alert_id, self.clock())
        else:
            try:
                await self.repo.mark_sent(alert_id, int(getattr(msg, "message_id", msg)), self.clock())
            except Exception:
                await self.repo.mark_unknown_fresh(alert_id, self.clock())

    async def close(self):
        self._stop.set()
        self._wake.set()

    async def stop(self):
        await self.close()
        if self.task:
            await self.task


async def admin_alert_text_from_repo(repo: HealthRepository, alert_id: int) -> str:
    async with repo.sessions() as session:
        alert = await session.get(AdminAlertRecord, alert_id)
        incident = await session.get(OutageIncidentRecord, alert.incident_id) if alert else None
        if alert is None or incident is None:
            return ""
        return admin_alert_text(alert, incident)


def _fmt_dt(value: datetime) -> str:
    return _aware_utc(value).astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M")


def _duration(start: datetime, end: datetime | None) -> str:
    seconds = max(0, int(((_aware_utc(end) if end else _aware_utc(start)) - _aware_utc(start)).total_seconds()))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ساعت و {minutes} دقیقه"
    if minutes:
        return f"{minutes} دقیقه"
    return f"{sec} ثانیه"


def admin_alert_text(alert: AdminAlertRecord, incident: OutageIncidentRecord) -> str:
    label = html.escape(SERVICE_LABELS.get(alert.service_key, alert.service_key), quote=True)
    if alert.kind is AdminAlertKind.RECOVERY:
        recovered_at = incident.recovered_at or alert.created_at
        return f"✅ <b>اتصال API بازیابی شد</b>\n\nسرویس: {label}\nمدت اختلال: {html.escape(_duration(incident.started_at, recovered_at))}\nزمان بازیابی: {_fmt_dt(recovered_at)}"
    if alert.kind is AdminAlertKind.OUTAGE_REPEAT:
        return f"🚨 <b>اختلال API همچنان ادامه دارد</b>\n\nسرویس: {label}\nمدت اختلال: {html.escape(_duration(incident.started_at, alert.created_at))}\nآخرین بررسی: {_fmt_dt(alert.created_at)}"
    return f"🚨 <b>اختلال API شناسایی شد</b>\n\nسرویس: {label}\nوضعیت: قطع\nزمان شروع: {_fmt_dt(incident.started_at)}\n\nتا زمان بازیابی، هر یک دقیقه یادآوری ارسال می‌شود."
