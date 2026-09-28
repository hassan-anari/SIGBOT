"""Phase 7 private report Telegram surfaces.

Callback payloads for personal reports contain period/page only.  Ownership is
always taken from the freshly resolved Actor.
"""
from datetime import datetime, timezone
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from chainura.application.access import Actor, ActorKind
from chainura.application.reporting import DestinationReportFilter, ReportFilter, destination_period_filter, period_filter
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import remove_menu, signalist_menu

router=Router(name="phase7-private-reports")

def _button(label,data): return InlineKeyboardButton(text=label,callback_data=data)
def _periods(prefix):
    return InlineKeyboardMarkup(inline_keyboard=[[_button(texts.REPORT_TODAY,f"{prefix}:today"),_button(texts.REPORT_7D,f"{prefix}:7d")],
        [_button(texts.REPORT_30D,f"{prefix}:30d")]])
def _actions(prefix,page=1,pages=1,back=None):
    rows=[[_button(texts.REPORT_HISTORY,f"{prefix}:hist:1")]]; nav=[]
    if page>1:nav.append(_button(texts.BUTTON_PREVIOUS,f"{prefix}:hist:{page-1}"))
    if page<pages:nav.append(_button(texts.BUTTON_NEXT,f"{prefix}:hist:{page+1}"))
    if nav:rows.insert(0,nav)
    if back:rows.append([_button(texts.BUTTON_BACK_STEP,back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
def _stored_filter(data):
    try:return ReportFilter(datetime.fromisoformat(data["p7_start"]),datetime.fromisoformat(data["p7_end"]))
    except (KeyError,TypeError,ValueError):return None

@router.message(F.text==texts.MENU_SIGNALIST_REPORT)
async def personal_entry(message:Message,actor:Actor,state:FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST or actor.signalist is None:
        await message.answer(texts.UNKNOWN,reply_markup=remove_menu());return
    await state.clear();await message.answer(texts.REPORT_PERIOD_PRIVATE,reply_markup=_periods("p7:s:summary"))

@router.callback_query(F.data.startswith("p7:s:"))
async def personal_callback(query:CallbackQuery,actor:Actor,state:FSMContext,reporting_service=None):
    await query.answer()
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST or actor.signalist is None:return
    parts=(query.data or "").split(":")
    if reporting_service is None or len(parts)<4:return await query.message.answer(texts.TEMPORARY_ERROR)
    action=parts[2]
    if action=="summary":
        try:flt=period_filter(parts[3],datetime.now(timezone.utc))
        except ValueError:return
        await state.update_data(p7_start=flt.start_utc.isoformat(),p7_end=flt.end_utc.isoformat())
        summary=await reporting_service.signalist_summary(actor.signalist.id,flt)
        await query.message.answer(texts.private_report_summary_text(summary,texts.SIGNALIST_REPORT_TITLE),reply_markup=_actions("p7:s"));return
    if action=="hist":
        flt=_stored_filter(await state.get_data())
        if flt is None:return await query.message.answer(texts.STALE_ACTION)
        page=await reporting_service.signalist_history(actor.signalist.id,flt,int(parts[3]))
        await query.message.answer(texts.private_history_text(page),reply_markup=_actions("p7:s",page.page,page.pages))

@router.callback_query(F.data.startswith("p7:d:"))
async def destination_callback(query:CallbackQuery,actor:Actor,state:FSMContext,reporting_service=None,destination_service=None):
    await query.answer()
    if actor.kind is not ActorKind.MAIN_ADMIN:return
    parts=(query.data or "").split(":")
    try:destination_id=int(parts[2])
    except (IndexError,ValueError):return
    if destination_id<1 or reporting_service is None or not await reporting_service.destination_exists(destination_id):
        return await query.message.answer(texts.REPORT_DESTINATION_MISSING)
    destination=await destination_service.get(destination_id)
    action=parts[3] if len(parts)>3 else "period"
    if action=="period":
        await state.clear();await state.update_data(p7_destination_id=destination_id)
        await query.message.answer(texts.REPORT_PERIOD_PRIVATE,reply_markup=_periods(f"p7:d:{destination_id}:summary"));return
    if action=="summary":
        try:flt=destination_period_filter(destination_id,parts[4],datetime.now(timezone.utc))
        except (IndexError,ValueError):return
        await state.update_data(p7_destination_id=destination_id,p7_start=flt.start_utc.isoformat(),p7_end=flt.end_utc.isoformat())
        summary=await reporting_service.destination_summary(flt)
        await query.message.answer(texts.private_report_summary_text(summary,texts.DESTINATION_REPORT_TITLE,destination.internal_name),
            reply_markup=_actions(f"p7:d:{destination_id}",back=f"p4:ds:view:{destination_id}"));return
    if action=="hist":
        data=await state.get_data()
        if data.get("p7_destination_id")!=destination_id:return await query.message.answer(texts.STALE_ACTION)
        base=_stored_filter(data)
        if base is None:return await query.message.answer(texts.STALE_ACTION)
        flt=DestinationReportFilter(destination_id,base.start_utc,base.end_utc)
        page=await reporting_service.destination_history(flt,int(parts[4]))
        await query.message.answer(texts.private_history_text(page,destination=True),
            reply_markup=_actions(f"p7:d:{destination_id}",page.page,page.pages,back=f"p4:ds:view:{destination_id}"))
