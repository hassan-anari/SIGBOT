"""Durable direct Signalist notification delivery."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime,timezone,timedelta
from html import escape
from sqlalchemy import select
from decimal import Decimal, ROUND_HALF_UP
from chainura.domain.monitoring import *
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.models import (SignalistNotificationRecord,SignalistRecord,
 SignalUpdateCandidateRecord,SignalEventRecord,SignalRecord,BreakevenPromptRecord)
from chainura.trade_id import format_trade_id
from chainura.application.monitoring import LEGACY_TERMINAL_INTERACTION_VERSION
from chainura.application.telegram_delivery import normalize_telegram_error

LOGGER=logging.getLogger("chainura.signalist_notification")

FSI="\u2068";PDI="\u2069"
def iso(value)->str:return f"{FSI}{escape(str(value))}{PDI}"


def _plain_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def fmt_decimal(value: Decimal) -> str:
    return iso(_plain_decimal(value))


def fmt_percent(value: Decimal) -> str:
    display_value = value.quantize(
        Decimal("0.001"),
        rounding=ROUND_HALF_UP,
    )
    if display_value == 0:
        display_value = Decimal("0")
    return fmt_decimal(display_value)

def event_text(kind, symbol, price, roi, tp_index=None, signal_id=None,*,position_action=True):
    if signal_id is None:
        raise ValueError("signal_id is required for event notification")
    trade = iso(format_trade_id(signal_id))
    symbol_text = iso(symbol)
    price_text = fmt_decimal(price)
    roi_text = fmt_percent(roi)
    if kind is SignalEventKind.TP_HIT:
        if tp_index is None or tp_index < 1:
            raise ValueError("tp_index is required for TP notification")
        head = (
            f"🎯 <b>تارگت {iso(tp_index)} {symbol_text} لمس شد</b>"
            f"\n\nقیمت رویداد: {price_text}"
            f"\nبازده اهرمی: {roi_text}٪"
        )
    elif kind is SignalEventKind.SL_HIT:
        head = (
            f"🛑 <b>حد ضرر {symbol_text} فعال شد</b>"
            f"\n\nقیمت رویداد: {price_text}"
            f"\nنتیجه: {roi_text}٪"
        )
    elif kind is SignalEventKind.BREAKEVEN:
        head = (
            f"⚖️ <b>سیگنال {symbol_text} در بریک‌ایون بسته شد</b>"
            f"\n\nقیمت خروج: {price_text}"
            f"\nنتیجه: {fmt_percent(Decimal('0'))}٪"
        )
    elif kind is SignalEventKind.RISK_FREE_HIT:
        head = (f"⚖️ <b>باقی‌مانده پوزیشن {symbol_text} در نقطه ورود بسته شد</b>"
                f"\n\nقیمت خروج: {price_text}\nنتیجه رویداد: {fmt_percent(Decimal('0'))}٪")
    else:
        raise ValueError("unsupported event kind")
    question=("اقدام پوزیشن را برای تارگت تأییدشده انتخاب کنید."
              if kind is SignalEventKind.TP_HIT and position_action
              else "آیا این آپدیت برای مقصدها منتشر شود؟")
    return f"شناسه معامله: {trade}\n" + head + "\n\n" + question

def prompt_text(symbol,entry,current,signal_id=None):
    if signal_id is None:
        raise ValueError("signal_id is required for Breakeven prompt")
    return f"شناسه معامله: {iso(format_trade_id(signal_id))}\n⚖️ قیمت {iso(symbol)} به محدوده بریک‌ایون بازگشته است.\n\nقیمت ورود: {iso(entry)}\nقیمت فعلی: {iso(current)}\n\nآیا بریک‌ایون ثبت شود؟"

class SignalistNotificationService:
    def __init__(self,sessions,transport,max_attempts=3,retry_base_seconds=.5,clock=lambda:datetime.now(timezone.utc)):
        self.sessions=sessions;self.transport=transport;self.max_attempts=max_attempts;self.retry_base=retry_base_seconds;self.clock=clock
    async def recover(self):
        async with self.sessions() as s,s.begin():
            rows=(await s.scalars(select(SignalistNotificationRecord).where(SignalistNotificationRecord.status==NotificationStatus.SENDING))).all()
            for row in rows:row.status=NotificationStatus.DELIVERY_UNKNOWN;row.updated_at=self.clock();row.last_error_category="interrupted_send"
    async def pending(self,limit=100):
        now=self.clock()
        async with self.sessions() as s:
            return tuple((await s.scalars(select(SignalistNotificationRecord.id).where(SignalistNotificationRecord.status.in_((NotificationStatus.PENDING,NotificationStatus.RETRY_WAIT)),(SignalistNotificationRecord.retry_at.is_(None))|(SignalistNotificationRecord.retry_at<=now)).order_by(SignalistNotificationRecord.created_at).limit(limit))).all())
    async def deliver(self,notification_id):
        now=self.clock()
        async with self.sessions() as s,s.begin():
            row=await s.get(SignalistNotificationRecord,notification_id);actor=await s.get(SignalistRecord,row.signalist_id) if row else None
            if row is None or row.status not in (NotificationStatus.PENDING,NotificationStatus.RETRY_WAIT):return
            if actor is None or actor.status is not SignalistStatus.ACTIVE:row.status=NotificationStatus.SKIPPED_INACTIVE;row.updated_at=now;return
            row.status=NotificationStatus.SENDING;row.attempt_count+=1;row.updated_at=now;telegram_id=actor.telegram_user_id
            if row.kind is NotificationKind.EVENT:
                candidate=await s.get(SignalUpdateCandidateRecord,row.candidate_id);event=await s.get(SignalEventRecord,candidate.event_id);signal=await s.get(SignalRecord,event.signal_id)
                if candidate.status is CandidateStatus.SUPERSEDED:row.status=NotificationStatus.SUPERSEDED;return
                text=event_text(event.kind,signal.canonical_symbol,event.roi_basis_price,event.leveraged_roi,event.tp_index,signal.id,
                    position_action=row.interaction_version!=LEGACY_TERMINAL_INTERACTION_VERSION);source=candidate
            else:
                prompt=await s.get(BreakevenPromptRecord,row.prompt_id);signal=await s.get(SignalRecord,prompt.signal_id)
                if prompt.status is not PromptStatus.PENDING_NOTIFICATION:row.status=NotificationStatus.SUPERSEDED;return
                text=prompt_text(signal.canonical_symbol,signal.entry_price,prompt.observed_price_at_creation,signal.id);source=prompt
        try:message_id=await self.transport.send(telegram_id,text,row.kind,row.candidate_id or row.prompt_id)
        except Exception as exc:
            failure=normalize_telegram_error(exc)
            async with self.sessions() as s,s.begin():
                current=await s.get(SignalistNotificationRecord,notification_id)
                current.last_error_category=failure.category;current.updated_at=self.clock()
                if failure.retry_after is not None:
                    current.status=NotificationStatus.RETRY_WAIT
                    backoff=self.retry_base*(2**max(0,current.attempt_count-1))
                    current.retry_at=self.clock()+timedelta(seconds=max(failure.retry_after,backoff))
                elif failure.definite_no_send:
                    current.status=NotificationStatus.FAILED;current.retry_at=None
                else:
                    current.status=NotificationStatus.DELIVERY_UNKNOWN;current.retry_at=None
            return
        async with self.sessions() as s,s.begin():
            current=await s.get(SignalistNotificationRecord,notification_id);current.status=NotificationStatus.SENT;current.telegram_message_id=message_id;current.sent_at=self.clock();current.updated_at=self.clock()
            if current.kind is NotificationKind.EVENT:
                candidate=await s.get(SignalUpdateCandidateRecord,current.candidate_id)
                if candidate.status is CandidateStatus.AWAITING_NOTIFICATION:candidate.status=CandidateStatus.AWAITING_DECISION;candidate.updated_at=self.clock()
            else:
                prompt=await s.get(BreakevenPromptRecord,current.prompt_id)
                if prompt.status is PromptStatus.PENDING_NOTIFICATION:prompt.status=PromptStatus.AWAITING_RESPONSE;prompt.notified_at=self.clock();prompt.telegram_message_id=message_id;prompt.updated_at=self.clock()

class SignalistNotificationWorker:
    def __init__(self,service,concurrency=3,sweep_seconds=5,lifecycle_reconcile=None):self.service=service;self.concurrency=concurrency;self.sweep=sweep_seconds;self.lifecycle_reconcile=lifecycle_reconcile;self._wake=asyncio.Event();self._stop=asyncio.Event();self.task=None
    def wake(self):self._wake.set()
    async def start(self):
        if self.task is None:self.task=asyncio.create_task(self._run())
    async def _run(self):
        sem=asyncio.Semaphore(self.concurrency)
        while not self._stop.is_set():
            if self.lifecycle_reconcile:
                try:await self.lifecycle_reconcile()
                except Exception as exc:
                    LOGGER.warning("lifecycle reconciliation failed category=%s",type(exc).__name__)
            ids=await self.service.pending()
            async def one(i):
                async with sem:await self.service.deliver(i)
            await asyncio.gather(*(one(i) for i in ids),return_exceptions=True);self._wake.clear()
            try:await asyncio.wait_for(self._wake.wait(),self.sweep)
            except asyncio.TimeoutError:pass
    async def stop(self):self._stop.set();self._wake.set();await self.task if self.task else asyncio.sleep(0)
