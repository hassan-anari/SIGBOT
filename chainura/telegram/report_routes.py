"""Phase 7 private report Telegram surfaces.

Personal callback/FSM data never contains an owner ID. Every query derives the
owner from the freshly resolved Actor. Destination context is revalidated for
every callback and custom-date message.
"""
from datetime import date, datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from chainura.application.access import Actor, ActorKind
from chainura.application.reporting import (DestinationReportFilter, ReportFilter, TEHRAN,
    destination_period_filter, period_filter)
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import remove_menu
from chainura.telegram.states import DestinationReportFlow, SignalistReportFlow

router = Router(name="phase7-private-reports")
PERIODS = frozenset({"today", "7d", "30d", "custom"})

def _button(label, data): return InlineKeyboardButton(text=label, callback_data=data)

def _periods(prefix):
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(texts.REPORT_TODAY, f"{prefix}:today"), _button(texts.REPORT_7D, f"{prefix}:7d")],
        [_button(texts.REPORT_30D, f"{prefix}:30d"), _button(texts.REPORT_CUSTOM, f"{prefix}:custom")],
    ])

def _summary_actions(prefix, detail_back):
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button(texts.REPORT_HISTORY, f"{prefix}:hist:1")],
        [_button(texts.BUTTON_BACK_STEP, detail_back)],
    ])

def _history_actions(prefix, page, pages, summary_back):
    rows=[]; nav=[]
    if page > 1: nav.append(_button(texts.BUTTON_PREVIOUS, f"{prefix}:hist:{page-1}"))
    if page < pages: nav.append(_button(texts.BUTTON_NEXT, f"{prefix}:hist:{page+1}"))
    if nav: rows.append(nav)
    rows.append([_button(texts.BUTTON_BACK_STEP, summary_back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _stored_filter(data):
    try:
        value=ReportFilter(datetime.fromisoformat(data["p7_start"]), datetime.fromisoformat(data["p7_end"]))
        return value if value.start_utc.tzinfo and value.end_utc.tzinfo and value.start_utc < value.end_utc else None
    except (KeyError, TypeError, ValueError): return None

def _range_labels(flt):
    start_label=flt.start_utc.astimezone(TEHRAN).date().isoformat()
    end_label=(flt.end_utc.astimezone(TEHRAN).date()-timedelta(days=1)).isoformat()
    return start_label,end_label

def _positive(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit(): return None
    parsed=int(value)
    return parsed if parsed > 0 else None

async def _stale(event):
    message=event.message if isinstance(event, CallbackQuery) else event
    if isinstance(message, Message): await message.answer(texts.STALE_ACTION)

async def _destination(destination_id, reporting_service, destination_service):
    if destination_id is None or reporting_service is None or destination_service is None: return None
    if not await reporting_service.destination_exists(destination_id): return None
    try: return await destination_service.get(destination_id)
    except Exception: return None

@router.message(F.text == texts.MENU_SIGNALIST_REPORT)
async def personal_entry(message: Message, actor: Actor, state: FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST or actor.signalist is None:
        await message.answer(texts.UNKNOWN, reply_markup=remove_menu()); return
    await state.clear()
    await message.answer(texts.REPORT_PERIOD_PRIVATE, reply_markup=_periods("p7:s:summary"))

@router.callback_query(F.data.startswith("p7:s:"))
async def personal_callback(query: CallbackQuery, actor: Actor, state: FSMContext, reporting_service=None):
    await query.answer()
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST or actor.signalist is None: return
    parts=(query.data or "").split(":")
    if reporting_service is None or len(parts) != 4 or parts[:2] != ["p7", "s"]: return await _stale(query)
    action, argument=parts[2],parts[3]
    if action == "period" and argument == "back":
        return await query.message.answer(texts.REPORT_PERIOD_PRIVATE, reply_markup=_periods("p7:s:summary"))
    if action == "summary":
        if argument == "back":
            flt=_stored_filter(await state.get_data())
            if flt is None: return await _stale(query)
        elif argument == "custom":
            await state.set_state(SignalistReportFlow.start_date)
            await state.update_data(p7_mode="signalist")
            return await query.message.answer(texts.REPORT_ASK_START)
        elif argument in PERIODS:
            flt=period_filter(argument, datetime.now(timezone.utc))
            await state.update_data(p7_period=argument, p7_start=flt.start_utc.isoformat(), p7_end=flt.end_utc.isoformat())
        else: return await _stale(query)
        summary=await reporting_service.signalist_summary(actor.signalist.id, flt)
        return await query.message.answer(texts.private_report_summary_text(summary,texts.SIGNALIST_REPORT_TITLE),
            reply_markup=_summary_actions("p7:s", "p7:s:period:back"))
    if action == "hist":
        page_number=_positive(argument)
        flt=_stored_filter(await state.get_data())
        if page_number is None or flt is None: return await _stale(query)
        page=await reporting_service.signalist_history(actor.signalist.id, flt, page_number)
        return await query.message.answer(texts.private_history_text(page),
            reply_markup=_history_actions("p7:s",page.page,page.pages,"p7:s:summary:back"))
    await _stale(query)

@router.message(SignalistReportFlow.start_date)
async def personal_custom_start(message: Message, actor: Actor, state: FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST or actor.signalist is None: return
    try: start=date.fromisoformat((message.text or "").strip())
    except ValueError: return await message.answer(texts.REPORT_INVALID_RANGE)
    await state.update_data(p7_mode="signalist",p7_custom_start=start.isoformat())
    await state.set_state(SignalistReportFlow.end_date)
    await message.answer(texts.REPORT_ASK_END)

@router.message(SignalistReportFlow.end_date)
async def personal_custom_end(message: Message, actor: Actor, state: FSMContext, reporting_service=None):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST or actor.signalist is None or reporting_service is None: return
    data=await state.get_data()
    try:
        start=date.fromisoformat(data["p7_custom_start"]); end=date.fromisoformat((message.text or "").strip())
        flt=period_filter("custom",datetime.now(timezone.utc),start=start,end=end)
    except (KeyError,TypeError,ValueError): return await message.answer(texts.REPORT_INVALID_RANGE)
    await state.set_state(None)
    await state.update_data(p7_period="custom",p7_start=flt.start_utc.isoformat(),p7_end=flt.end_utc.isoformat())
    summary=await reporting_service.signalist_summary(actor.signalist.id,flt)
    await message.answer(texts.private_report_summary_text(summary,texts.SIGNALIST_REPORT_TITLE),
        reply_markup=_summary_actions("p7:s","p7:s:period:back"))

@router.callback_query(F.data.startswith("p7:d:"))
async def destination_callback(query: CallbackQuery, actor: Actor, state: FSMContext, reporting_service=None,destination_service=None):
    await query.answer()
    if actor.kind is not ActorKind.MAIN_ADMIN: return
    parts=(query.data or "").split(":")
    if len(parts) not in {4,5} or parts[:2] != ["p7","d"]: return await _stale(query)
    destination_id=_positive(parts[2]); destination=await _destination(destination_id,reporting_service,destination_service)
    if destination is None: return await _stale(query)
    action=parts[3]
    if action == "period" and len(parts)==4:
        await state.clear(); await state.update_data(p7_destination_id=destination_id,p7_mode="destination")
        return await query.message.answer(texts.REPORT_PERIOD_PRIVATE,reply_markup=_periods(f"p7:d:{destination_id}:summary"))
    if len(parts)!=5: return await _stale(query)
    argument=parts[4]
    if action == "summary":
        if argument == "back":
            data=await state.get_data()
            if data.get("p7_destination_id") != destination_id: return await _stale(query)
            base=_stored_filter(data)
            if base is None: return await _stale(query)
            flt=DestinationReportFilter(destination_id,base.start_utc,base.end_utc)
        elif argument == "custom":
            await state.set_state(DestinationReportFlow.start_date)
            await state.update_data(p7_destination_id=destination_id,p7_mode="destination")
            return await query.message.answer(texts.REPORT_ASK_START)
        elif argument in PERIODS:
            flt=destination_period_filter(destination_id,argument,datetime.now(timezone.utc))
            await state.update_data(p7_destination_id=destination_id,p7_period=argument,p7_start=flt.start_utc.isoformat(),p7_end=flt.end_utc.isoformat())
        else: return await _stale(query)
        performance=await reporting_service.destination_performance(flt)
        start_label,end_label=_range_labels(flt)
        return await query.message.answer(
            texts.destination_performance_text(performance,destination.internal_name,start_label,end_label),
            reply_markup=_summary_actions(f"p7:d:{destination_id}",f"p4:ds:view:{destination_id}"))
    if action == "hist":
        page_number=_positive(argument); data=await state.get_data()
        if page_number is None or data.get("p7_destination_id") != destination_id: return await _stale(query)
        base=_stored_filter(data)
        if base is None: return await _stale(query)
        page=await reporting_service.destination_history(DestinationReportFilter(destination_id,base.start_utc,base.end_utc),page_number)
        return await query.message.answer(texts.private_history_text(page,destination=True),
            reply_markup=_history_actions(f"p7:d:{destination_id}",page.page,page.pages,f"p7:d:{destination_id}:summary:back"))
    await _stale(query)

async def _destination_custom_context(message,actor,state,reporting_service,destination_service):
    if actor.kind is not ActorKind.MAIN_ADMIN: return None
    data=await state.get_data(); destination_id=data.get("p7_destination_id")
    if not isinstance(destination_id,int) or destination_id < 1: return None
    destination=await _destination(destination_id,reporting_service,destination_service)
    if destination is None: await message.answer(texts.REPORT_DESTINATION_MISSING)
    return (data,destination_id,destination) if destination else None

@router.message(DestinationReportFlow.start_date)
async def destination_custom_start(message:Message,actor:Actor,state:FSMContext,reporting_service=None,destination_service=None):
    context=await _destination_custom_context(message,actor,state,reporting_service,destination_service)
    if context is None: return
    try: start=date.fromisoformat((message.text or "").strip())
    except ValueError: return await message.answer(texts.REPORT_INVALID_RANGE)
    await state.update_data(p7_custom_start=start.isoformat())
    await state.set_state(DestinationReportFlow.end_date)
    await message.answer(texts.REPORT_ASK_END)

@router.message(DestinationReportFlow.end_date)
async def destination_custom_end(message:Message,actor:Actor,state:FSMContext,reporting_service=None,destination_service=None):
    context=await _destination_custom_context(message,actor,state,reporting_service,destination_service)
    if context is None:return
    data,destination_id,destination=context
    try:
        start=date.fromisoformat(data["p7_custom_start"]);end=date.fromisoformat((message.text or "").strip())
        flt=destination_period_filter(destination_id,"custom",datetime.now(timezone.utc),start,end)
    except (KeyError,TypeError,ValueError):return await message.answer(texts.REPORT_INVALID_RANGE)
    await state.set_state(None);await state.update_data(p7_period="custom",p7_start=flt.start_utc.isoformat(),p7_end=flt.end_utc.isoformat())
    performance=await reporting_service.destination_performance(flt)
    start_label,end_label=_range_labels(flt)
    await message.answer(
        texts.destination_performance_text(performance,destination.internal_name,start_label,end_label),
        reply_markup=_summary_actions(f"p7:d:{destination_id}",f"p4:ds:view:{destination_id}"))
