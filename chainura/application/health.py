from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import async_sessionmaker
from chainura.domain.destination import Exchange
from chainura.domain.health import *
from chainura.persistence.models import ServiceHealthRecord, OutageIncidentRecord, AdminAlertRecord
SERVICE_LABELS={"ourbit_prices":"قیمت Ourbit","ourbit_catalog":"فهرست قراردادهای Ourbit","toobit_prices":"قیمت Toobit","toobit_catalog":"فهرست قراردادهای Toobit","lbank_prices":"قیمت LBank","lbank_catalog":"فهرست قراردادهای LBank"}
def service_key(exchange:Exchange,operation:ApiOperation)->str: return f"{exchange.value}_{operation.value}"
class HealthRepository:
    def __init__(self,sessions:async_sessionmaker): self.sessions=sessions
    async def observe(self,obs:HealthObservation):
        now=obs.observed_at; cat=sanitize_error_category(obs.error_category)
        async with self.sessions() as s,s.begin():
            row=await s.get(ServiceHealthRecord,obs.service_key)
            if row is None:
                row=ServiceHealthRecord(service_key=obs.service_key,exchange=obs.exchange,operation=obs.operation,status=ServiceHealthStatus.UNKNOWN,created_at=now,updated_at=now,consecutive_successes=0,consecutive_failures=0);s.add(row);await s.flush()
            was_down=row.status is ServiceHealthStatus.DOWN and row.current_incident_id is not None
            row.last_attempt_at=now;row.updated_at=now;row.last_error_category=cat
            if obs.status is ServiceHealthStatus.DOWN:
                row.last_failure_at=now;row.consecutive_failures+=1;row.consecutive_successes=0
                incident=await s.get(OutageIncidentRecord,row.current_incident_id) if row.current_incident_id else None
                if incident is None:
                    incident=OutageIncidentRecord(service_key=obs.service_key,exchange=obs.exchange,operation=obs.operation,started_at=now,initial_error_category=cat,latest_error_category=cat,created_at=now,updated_at=now);s.add(incident);await s.flush();row.current_incident_id=incident.id;row.unhealthy_since=now;row.next_repeat_at=now+timedelta(seconds=REPEAT_SECONDS);row.status=obs.status;s.add(AdminAlertRecord(incident_id=incident.id,service_key=obs.service_key,kind=AdminAlertKind.OUTAGE_INITIAL,repeat_sequence=0,status=AdminAlertStatus.PENDING,attempts=0,sanitized_error=cat,created_at=now,updated_at=now))
                else:
                    incident.latest_error_category=cat;incident.updated_at=now
                row.status=obs.status
                return
            row.status=obs.status
            row.last_success_at=now;row.consecutive_successes+=1;row.consecutive_failures=0
            if was_down:
                incident=await s.get(OutageIncidentRecord,row.current_incident_id)
                if incident and incident.recovered_at is None:
                    incident.recovered_at=now;incident.updated_at=now;incident.latest_error_category=cat
                    s.add(AdminAlertRecord(incident_id=incident.id,service_key=obs.service_key,kind=AdminAlertKind.RECOVERY,repeat_sequence=0,status=AdminAlertStatus.PENDING,attempts=0,sanitized_error=cat,created_at=now,updated_at=now))
                row.current_incident_id=None;row.unhealthy_since=None;row.next_repeat_at=None
    async def enqueue_due_repeats(self,now:datetime)->int:
        count=0
        async with self.sessions() as s,s.begin():
            rows=(await s.scalars(select(ServiceHealthRecord).where(ServiceHealthRecord.status==ServiceHealthStatus.DOWN,ServiceHealthRecord.current_incident_id.is_not(None),ServiceHealthRecord.next_repeat_at<=now))).all()
            for row in rows:
                seq=(await s.scalar(select(AdminAlertRecord.repeat_sequence).where(AdminAlertRecord.incident_id==row.current_incident_id,AdminAlertRecord.kind==AdminAlertKind.OUTAGE_REPEAT).order_by(AdminAlertRecord.repeat_sequence.desc()).limit(1))) or 0
                s.add(AdminAlertRecord(incident_id=row.current_incident_id,service_key=row.service_key,kind=AdminAlertKind.OUTAGE_REPEAT,repeat_sequence=seq+1,status=AdminAlertStatus.PENDING,attempts=0,sanitized_error=row.last_error_category,created_at=now,updated_at=now));row.next_repeat_at=now+timedelta(seconds=REPEAT_SECONDS);row.updated_at=now;count+=1
        return count
    async def list_health(self):
        async with self.sessions() as s: return tuple((await s.scalars(select(ServiceHealthRecord).order_by(ServiceHealthRecord.service_key))).all())
    async def recover_sending(self,now):
        async with self.sessions() as s,s.begin(): await s.execute(update(AdminAlertRecord).where(AdminAlertRecord.status==AdminAlertStatus.SENDING).values(status=AdminAlertStatus.DELIVERY_UNKNOWN,updated_at=now))
    async def due_alerts(self,now,limit):
        async with self.sessions() as s: return tuple((await s.scalars(select(AdminAlertRecord).where(AdminAlertRecord.status.in_((AdminAlertStatus.PENDING,AdminAlertStatus.RETRY_WAIT)),(AdminAlertRecord.retry_at.is_(None)) | (AdminAlertRecord.retry_at<=now)).order_by(AdminAlertRecord.created_at,AdminAlertRecord.id).limit(limit))).all())
    async def mark_sending(self,id,now):
        async with self.sessions() as s,s.begin():
            row=await s.get(AdminAlertRecord,id)
            if not row or row.status in TERMINAL_ALERT_STATUSES:return None
            row.status=AdminAlertStatus.SENDING;row.attempts+=1;row.updated_at=now;return row
    async def mark_sent(self,id,msg_id,now):
        async with self.sessions() as s,s.begin():
            row=await s.get(AdminAlertRecord,id); row.status=AdminAlertStatus.SENT; row.telegram_message_id=msg_id; row.sent_at=now; row.updated_at=now
    async def mark_unknown(self,id,now):
        async with self.sessions() as s,s.begin(): row=await s.get(AdminAlertRecord,id); row.status=AdminAlertStatus.DELIVERY_UNKNOWN; row.updated_at=now
    async def mark_retry_or_failed(self,id,now,max_attempts,base):
        async with self.sessions() as s,s.begin():
            row=await s.get(AdminAlertRecord,id)
            if row.attempts>=max_attempts: row.status=AdminAlertStatus.FAILED; row.retry_at=None
            else: row.status=AdminAlertStatus.RETRY_WAIT; row.retry_at=now+timedelta(seconds=base*(2**max(0,row.attempts-1)))
            row.updated_at=now
class AdminAlertWorker:
    def __init__(self,repo:HealthRepository,transport,admin_id:int,clock=lambda:datetime.now(timezone.utc),concurrency=1,max_attempts=3,retry_base_seconds=.5,sweep_seconds=5):
        self.repo=repo;self.transport=transport;self.admin_id=admin_id;self.clock=clock;self.concurrency=concurrency;self.max_attempts=max_attempts;self.retry_base_seconds=retry_base_seconds;self.sweep_seconds=sweep_seconds;self._wake=asyncio.Event();self._stop=asyncio.Event();self.task=None
    def wake(self): self._wake.set()
    async def start(self): await self.repo.recover_sending(self.clock()); self.task=asyncio.create_task(self._run())
    async def _run(self):
        while not self._stop.is_set():
            now=self.clock(); await self.repo.enqueue_due_repeats(now); alerts=await self.repo.due_alerts(now,self.concurrency)
            for alert in alerts: await self._send(alert.id)
            try: await asyncio.wait_for(self._wake.wait(),self.sweep_seconds); self._wake.clear()
            except TimeoutError: pass
    async def _send(self,alert_id):
        now=self.clock(); row=await self.repo.mark_sending(alert_id,now)
        if not row:return
        try:
            msg=await self.transport.send_message(self.admin_id,admin_alert_text(row,self.clock()))
            await self.repo.mark_sent(alert_id,getattr(msg,"message_id",msg),self.clock())
        except TimeoutError: await self.repo.mark_unknown(alert_id,self.clock())
        except Exception: await self.repo.mark_retry_or_failed(alert_id,self.clock(),self.max_attempts,self.retry_base_seconds)
    async def close(self): self._stop.set(); self._wake.set();
    async def stop(self):
        await self.close()
        if self.task: await self.task
def admin_alert_text(row,now):
    label=SERVICE_LABELS.get(row.service_key,row.service_key); kind=row.kind
    if kind is AdminAlertKind.RECOVERY: return f"✅ <b>اتصال API بازیابی شد</b>\n\nسرویس: {label}\nزمان بازیابی: {now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Tehran')).strftime('%Y-%m-%d %H:%M')}"
    if kind is AdminAlertKind.OUTAGE_REPEAT: return f"🚨 <b>اختلال API همچنان ادامه دارد</b>\n\nسرویس: {label}\nآخرین بررسی: {now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Tehran')).strftime('%Y-%m-%d %H:%M')}"
    return f"🚨 <b>اختلال API شناسایی شد</b>\n\nسرویس: {label}\nوضعیت: قطع\nتا زمان بازیابی، هر یک دقیقه یادآوری ارسال می‌شود."
