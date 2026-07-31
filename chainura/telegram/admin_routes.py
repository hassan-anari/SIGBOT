from __future__ import annotations
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from chainura.application.access import Actor, ActorKind
from chainura.application.pagination import PageRequest
from chainura.domain.destination import DestinationAccessLevel, DestinationStatus, DestinationType, Exchange
from chainura.domain.signalist import SignalistStatus
from chainura.errors import (ApplicationError, DestinationAssignmentNotFoundError,
    DuplicateDestinationAssignmentError, DuplicateDestinationChatIdError,
    DuplicateDestinationInternalNameError, DuplicateTelegramUserIdError,
    SignalistNotEligibleForAssignmentError)
from chainura.telegram.middlewares import CallbackAcknowledger
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.callbacks import Phase4Callback, parse_phase4
from chainura.telegram.presentation.keyboards import admin_menu, remove_menu
from chainura.telegram.states import (AdminDestinationCreate, AdminDestinationEdit,
                                      AdminDestinationPriority, AdminSignalistCreate)

router=Router(name="phase4-admin-management")
def cb(ns,action,*args): return Phase4Callback(ns,action,tuple(str(x) for x in args)).encode()
def markup(rows): return InlineKeyboardMarkup(inline_keyboard=rows)
def button(label,data): return InlineKeyboardButton(text=label,callback_data=data)

async def require_admin(event,actor:Actor,ack:CallbackAcknowledger|None=None)->bool:
    if actor.kind is ActorKind.MAIN_ADMIN: return True
    denial={ActorKind.ACTIVE_SIGNALIST:texts.UNKNOWN,ActorKind.INACTIVE_SIGNALIST:texts.INACTIVE,
            ActorKind.ARCHIVED_SIGNALIST:texts.ARCHIVED,ActorKind.UNKNOWN:texts.UNKNOWN}[actor.kind]
    if ack: await ack.answer_once(denial)
    target=event.message if isinstance(event,CallbackQuery) else event
    if isinstance(target,Message): await target.answer(denial,reply_markup=remove_menu())
    return False

def list_keyboard(kind,items,page,filter_code,include_archive=False):
    rows=[[button(texts.safe(x.display_name if kind=="sl" else x.internal_name),cb(kind,"view",x.id))] for x in items]
    filters=[button(texts.BUTTON_ALL,cb(kind,"list","a",1)),button(texts.BUTTON_ACTIVE,cb(kind,"list","v",1)),button(texts.BUTTON_INACTIVE,cb(kind,"list","i",1))]
    if include_archive: filters.append(button(texts.BUTTON_ARCHIVED,cb(kind,"list","r",1)))
    rows.append(filters); nav=[]
    if page.page>1: nav.append(button(texts.BUTTON_PREVIOUS,cb(kind,"list",filter_code,page.page-1)))
    if page.page<page.pages: nav.append(button(texts.BUTTON_NEXT,cb(kind,"list",filter_code,page.page+1)))
    if nav: rows.append(nav)
    rows.append([button(texts.BUTTON_ADD,cb(kind,"create"))])
    return markup(rows)

async def show_signalists(message,service,filter_code="a",page_no=1):
    statuses={"a":None,"v":SignalistStatus.ACTIVE,"i":SignalistStatus.INACTIVE,"r":SignalistStatus.ARCHIVED}
    page=await service.list_page(statuses[filter_code],PageRequest(page_no))
    body="\n".join(texts.signalist_line(x) for x in page.items) or texts.EMPTY_LIST
    await message.answer(texts.page_title(texts.SIGNALIST_LIST_TITLE,page.page,page.pages)+"\n"+body,
                         reply_markup=list_keyboard("sl",page.items,page,filter_code,True))

async def show_destinations(message,service,filter_code="a",page_no=1):
    statuses={"a":None,"v":DestinationStatus.ACTIVE,"i":DestinationStatus.INACTIVE}
    page=await service.list_page(statuses[filter_code],PageRequest(page_no))
    body="\n".join(texts.destination_line(x) for x in page.items) or texts.EMPTY_LIST
    await message.answer(texts.page_title(texts.DESTINATION_LIST_TITLE,page.page,page.pages)+"\n"+body,
                         reply_markup=list_keyboard("ds",page.items,page,filter_code))

@router.message(F.text==texts.MENU_ADMIN_SIGNALISTS)
async def signalists_menu(message:Message,actor:Actor,state:FSMContext,signalist_service):
    if not await require_admin(message,actor): return
    await state.clear(); await show_signalists(message,signalist_service)

@router.message(F.text==texts.MENU_ADMIN_DESTINATIONS)
async def destinations_menu(message:Message,actor:Actor,state:FSMContext,destination_service):
    if not await require_admin(message,actor): return
    await state.clear(); await show_destinations(message,destination_service)

@router.callback_query(F.data.startswith("p4:"))
async def callbacks(query:CallbackQuery,actor:Actor,callback_ack:CallbackAcknowledger,state:FSMContext,
                    signalist_service,destination_service,assignment_service,admin_signalist_service):
    parsed=parse_phase4(query.data)
    if parsed is None: await callback_ack.answer_once(texts.INVALID_CALLBACK); return
    if not await require_admin(query,actor,callback_ack): return
    await callback_ack.answer_once()
    if not isinstance(query.message,Message): return
    a=parsed.arguments
    try:
        if parsed.namespace=="nav" and parsed.action=="cancel":
            await state.clear(); await query.message.answer(texts.FLOW_CANCELLED,reply_markup=admin_menu()); return
        elif parsed.namespace=="nav" and parsed.action=="confirm" and a[0]=="sl":
            data=await state.get_data()
            try: await admin_signalist_service.create(data["telegram_user_id"],data["display_name"],data.get("telegram_username"))
            except DuplicateTelegramUserIdError: await state.clear(); await query.message.answer(texts.DUPLICATE_SIGNALIST); return
            except ApplicationError: await state.clear(); await query.message.answer(texts.INVALID_INPUT); return
            await state.clear(); await query.message.answer(texts.CREATED_SUCCESS); return
        elif parsed.namespace=="nav" and parsed.action=="type":
            await state.update_data(destination_type=a[0]); await state.set_state(AdminDestinationCreate.access_level)
            await query.message.answer(texts.ASK_DESTINATION_ACCESS,reply_markup=markup([[button(texts.ACCESS_PUBLIC,cb("nav","access","public")),button(texts.ACCESS_PRIVATE,cb("nav","access","private"))]])); return
        elif parsed.namespace=="nav" and parsed.action=="access":
            await state.update_data(access_level=a[0]); await state.set_state(AdminDestinationCreate.daily_limit); await query.message.answer(texts.ASK_DAILY_LIMIT); return
        elif parsed.namespace=="nav" and parsed.action=="exchange":
            data=await state.get_data(); selected=list(data.get("exchanges",[])); code=a[0]
            if code in selected: selected.remove(code)
            else: selected.append(code)
            await state.update_data(exchanges=selected)
            await query.message.answer(texts.priority_current(selected),reply_markup=priority_keyboard(selected)); return
        elif parsed.namespace=="nav" and parsed.action=="confirm" and a[0]=="ds":
            data=await state.get_data()
            try:
                await destination_service.create(data["internal_name"],data["telegram_chat_id"],DestinationType(data["destination_type"]),DestinationAccessLevel(data["access_level"]),data["daily_limit"],tuple(Exchange(x) for x in data["exchanges"]))
            except DuplicateDestinationChatIdError: await state.clear(); await query.message.answer(texts.DUPLICATE_DESTINATION_CHAT); return
            except DuplicateDestinationInternalNameError: await state.clear(); await query.message.answer(texts.DUPLICATE_DESTINATION_NAME); return
            except ApplicationError: await state.clear(); await query.message.answer(texts.INVALID_INPUT); return
            await state.clear(); await query.message.answer(texts.CREATED_SUCCESS); return
        elif parsed.namespace=="nav" and parsed.action=="confirm" and a[0]=="edit":
            from datetime import datetime
            data=await state.get_data()
            try: await destination_service.update_metadata(data["destination_id"],data["internal_name"],DestinationType(data["destination_type"]),DestinationAccessLevel(data["access_level"]),data["daily_limit"],datetime.fromisoformat(data["expected_updated_at"]))
            except DuplicateDestinationInternalNameError: await state.clear(); await query.message.answer(texts.DUPLICATE_DESTINATION_NAME); return
            except ApplicationError: await state.clear(); await query.message.answer(texts.STALE_ACTION); return
            await state.clear(); await query.message.answer(texts.CREATED_SUCCESS); return
        if parsed.namespace=="sl" and parsed.action=="list": await show_signalists(query.message,signalist_service,a[0],int(a[1]))
        elif parsed.namespace=="ds" and parsed.action=="list": await show_destinations(query.message,destination_service,a[0],int(a[1]))
        elif parsed.action=="create" and parsed.namespace=="sl":
            await state.clear(); await state.set_state(AdminSignalistCreate.telegram_user_id); await query.message.answer(texts.ASK_SIGNALIST_ID)
        elif parsed.action=="create" and parsed.namespace=="ds":
            await state.clear(); await state.set_state(AdminDestinationCreate.internal_name); await query.message.answer(texts.ASK_DESTINATION_NAME)
        elif parsed.namespace=="sl" and parsed.action=="view":
            item=await signalist_service.get_by_id(int(a[0])); count=await admin_signalist_service.assignment_count(item.id)
            content=texts.signalist_detail(item,count)
            rows=[]
            if item.status is SignalistStatus.ACTIVE: rows.append([button(texts.BUTTON_DEACTIVATE,cb("sl","status",item.id,"deactivate")),button(texts.BUTTON_ARCHIVE,cb("sl","status",item.id,"archive","confirm"))])
            elif item.status is SignalistStatus.INACTIVE: rows.append([button(texts.BUTTON_ACTIVATE,cb("sl","status",item.id,"activate")),button(texts.BUTTON_ARCHIVE,cb("sl","status",item.id,"archive","confirm"))])
            await query.message.answer(content,reply_markup=markup(rows) if rows else None)
        elif parsed.namespace=="sl" and parsed.action=="status":
            method={"activate":signalist_service.activate,"deactivate":signalist_service.deactivate,"archive":signalist_service.archive}[a[1]]
            await method(int(a[0])); await query.message.answer(texts.CREATED_SUCCESS)
        elif parsed.namespace=="ds" and parsed.action=="view":
            detail=await destination_service.detail(int(a[0])); d=detail.destination
            content=texts.destination_detail(detail)
            status="activate" if d.status is DestinationStatus.INACTIVE else "deactivate"; label=texts.BUTTON_ACTIVATE if status=="activate" else texts.BUTTON_DEACTIVATE
            rows=[[button(label,cb("ds","status",d.id,status))],
                  [button(texts.BUTTON_EDIT,cb("ds","edit",d.id)),button(texts.BUTTON_PRIORITY,cb("ds","priority",d.id))],
                  [button(texts.BUTTON_ASSIGNMENTS,cb("as","list",d.id,"assigned",1))]]
            await query.message.answer(content,reply_markup=markup(rows))
        elif parsed.namespace=="ds" and parsed.action=="status":
            await (destination_service.activate if a[1]=="activate" else destination_service.deactivate)(int(a[0])); await query.message.answer(texts.CREATED_SUCCESS)
        elif parsed.namespace=="ds" and parsed.action=="edit":
            d=await destination_service.get(int(a[0])); await state.clear()
            await state.update_data(destination_id=d.id,expected_updated_at=d.updated_at.isoformat(),telegram_chat_id=d.telegram_chat_id)
            await state.set_state(AdminDestinationEdit.internal_name); await query.message.answer(texts.ASK_DESTINATION_NAME)
        elif parsed.namespace=="ds" and parsed.action=="priority":
            detail=await destination_service.detail(int(a[0])); await state.clear()
            selected=[x.value for x in detail.priority.exchanges]
            await state.update_data(destination_id=detail.destination.id,expected_updated_at=detail.destination.updated_at.isoformat(),exchanges=selected)
            await state.set_state(AdminDestinationPriority.editing); await query.message.answer(texts.EDIT_PRIORITY,reply_markup=priority_edit_keyboard(detail.destination.id,selected))
        elif parsed.namespace=="dp" and parsed.action in {"add","remove","move"}:
            data=await state.get_data()
            if int(a[0])!=data.get("destination_id"): await query.message.answer(texts.FLOW_EXPIRED); return
            mapping={"o":"ourbit","t":"toobit","l":"lbank"}; selected=list(data.get("exchanges",[])); value=mapping[a[1]]
            if parsed.action=="add" and value not in selected: selected.append(value)
            elif parsed.action=="remove" and value in selected: selected.remove(value)
            elif parsed.action=="move" and value in selected:
                old=selected.index(value); new=old+(-1 if a[2]=="u" else 1)
                if 0<=new<len(selected): selected[old],selected[new]=selected[new],selected[old]
            await state.update_data(exchanges=selected); await query.message.answer(texts.priority_current(selected),reply_markup=priority_edit_keyboard(int(a[0]),selected))
        elif parsed.namespace=="dp" and parsed.action=="confirm":
            from datetime import datetime
            data=await state.get_data()
            if not data.get("exchanges"): await query.message.answer(texts.INVALID_INPUT); return
            await destination_service.replace_priority(int(a[0]),tuple(Exchange(x) for x in data["exchanges"]),datetime.fromisoformat(data["expected_updated_at"]))
            await state.clear(); await query.message.answer(texts.CREATED_SUCCESS)
        elif parsed.namespace=="as" and parsed.action=="list":
            did=int(a[0]); page=await (assignment_service.list_assigned if a[1]=="assigned" else assignment_service.list_eligible)(did,PageRequest(int(a[2])))
            body="\n".join(texts.signalist_line(x) for x in page.items) or texts.EMPTY_LIST
            rows=[[button(texts.safe(x.display_name),cb("as","remove" if a[1]=="assigned" else "add",did,x.id))] for x in page.items]
            rows.append([button(texts.SHOW_ELIGIBLE if a[1]=="assigned" else texts.SHOW_ASSIGNED,cb("as","list",did,"eligible" if a[1]=="assigned" else "assigned",1))])
            await query.message.answer(body,reply_markup=markup(rows))
        elif parsed.namespace=="as" and parsed.action=="add":
            await assignment_service.assign(int(a[0]),int(a[1])); await query.message.answer(texts.CREATED_SUCCESS)
        elif parsed.namespace=="as" and parsed.action=="remove":
            await query.message.answer(texts.CONFIRM_UNASSIGN,reply_markup=markup([[button(texts.BUTTON_CONFIRM,cb("as","confirm","remove",a[0],a[1]))]]))
        elif parsed.namespace=="as" and parsed.action=="confirm":
            await assignment_service.unassign(int(a[1]),int(a[2])); await query.message.answer(texts.CREATED_SUCCESS)
    except DuplicateDestinationAssignmentError: await query.message.answer(texts.ASSIGNMENT_DUPLICATE)
    except DestinationAssignmentNotFoundError: await query.message.answer(texts.ASSIGNMENT_MISSING)
    except SignalistNotEligibleForAssignmentError: await query.message.answer(texts.ASSIGNMENT_INELIGIBLE)
    except ApplicationError: await query.message.answer(texts.STALE_ACTION)

@router.message(AdminSignalistCreate.telegram_user_id)
async def sl_id(message:Message,actor:Actor,state:FSMContext):
    if not await require_admin(message,actor): return
    try: value=int(message.text or ""); assert str(value)==(message.text or "").strip() and value>0 and value<=2**63-1
    except (ValueError,AssertionError): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(telegram_user_id=value); await state.set_state(AdminSignalistCreate.display_name); await message.answer(texts.ASK_SIGNALIST_NAME)
@router.message(AdminSignalistCreate.display_name)
async def sl_name(message:Message,actor:Actor,state:FSMContext):
    if not await require_admin(message,actor): return
    if not message.text or not message.text.strip(): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(display_name=message.text.strip()); await state.set_state(AdminSignalistCreate.telegram_username); await message.answer(texts.ASK_SIGNALIST_USERNAME)
@router.message(AdminSignalistCreate.telegram_username)
async def sl_username(message:Message,actor:Actor,state:FSMContext):
    if not await require_admin(message,actor): return
    raw=(message.text or "").strip(); username=None if raw==texts.SKIP else raw.removeprefix("@") or None
    await state.update_data(telegram_username=username); data=await state.get_data(); await state.set_state(AdminSignalistCreate.review)
    await message.answer(texts.signalist_review(data,username),reply_markup=markup([[button(texts.BUTTON_CONFIRM,"p4:nav:confirm:sl"),button(texts.BUTTON_CANCEL,"p4:nav:cancel:sl")]]))

@router.message(AdminDestinationCreate.internal_name)
async def ds_name(message:Message,actor:Actor,state:FSMContext):
    if not await require_admin(message,actor): return
    if not message.text or not message.text.strip(): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(internal_name=message.text.strip()); await state.set_state(AdminDestinationCreate.telegram_chat_id); await message.answer(texts.ASK_DESTINATION_CHAT_ID)
@router.message(AdminDestinationCreate.telegram_chat_id)
async def ds_chat(message:Message,actor:Actor,state:FSMContext):
    if not await require_admin(message,actor): return
    try: value=int(message.text or ""); assert value!=0 and -(2**63)<=value<=2**63-1
    except (ValueError,AssertionError): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(telegram_chat_id=value); await state.set_state(AdminDestinationCreate.destination_type)
    await message.answer(texts.ASK_DESTINATION_TYPE,reply_markup=markup([[button(texts.TYPE_CHANNEL,cb("nav","type","channel")),button(texts.TYPE_GROUP,cb("nav","type","group"))]]))

def priority_keyboard(selected):
    rows=[[button(("✓ " if x in selected else "")+x,cb("nav","exchange",x))] for x in ("ourbit","toobit","lbank")]
    if selected: rows.append([button(texts.BUTTON_CONFIRM,cb("nav","confirm","ds"))])
    rows.append([button(texts.BUTTON_CANCEL,cb("nav","cancel","ds"))]); return markup(rows)

def priority_edit_keyboard(did,selected):
    codes={"ourbit":"o","toobit":"t","lbank":"l"}; rows=[]
    for value in selected:
        code=codes[value]; rows.append([button("▲",cb("dp","move",did,code,"u")),button(value,cb("dp","remove",did,code)),button("▼",cb("dp","move",did,code,"d"))])
    for value,code in codes.items():
        if value not in selected: rows.append([button(texts.ADD_EXCHANGE.format(exchange=value),cb("dp","add",did,code))])
    if selected: rows.append([button(texts.BUTTON_CONFIRM,cb("dp","confirm",did))])
    return markup(rows)

@router.message(AdminDestinationCreate.daily_limit)
async def ds_limit(message:Message,actor:Actor,state:FSMContext):
    if not await require_admin(message,actor): return
    try: value=int(message.text or ""); assert str(value)==(message.text or "").strip() and 0<value<=2**63-1
    except (ValueError,AssertionError): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(daily_limit=value,exchanges=[]); await state.set_state(AdminDestinationCreate.priority)
    await message.answer(texts.ASK_PRIORITY,reply_markup=priority_keyboard([]))

@router.message(AdminDestinationEdit.internal_name)
async def edit_name(message:Message,actor:Actor,state:FSMContext):
    if not await require_admin(message,actor): return
    if not message.text or not message.text.strip(): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(internal_name=message.text.strip()); await state.set_state(AdminDestinationEdit.destination_type)
    await message.answer(texts.ASK_DESTINATION_TYPE_TEXT)
@router.message(AdminDestinationEdit.destination_type)
async def edit_type(message:Message,actor:Actor,state:FSMContext):
    if not await require_admin(message,actor): return
    try: DestinationType((message.text or "").strip())
    except ValueError: await message.answer(texts.INVALID_INPUT); return
    await state.update_data(destination_type=message.text.strip()); await state.set_state(AdminDestinationEdit.access_level); await message.answer(texts.ASK_DESTINATION_ACCESS_TEXT)
@router.message(AdminDestinationEdit.access_level)
async def edit_access(message:Message,actor:Actor,state:FSMContext):
    if not await require_admin(message,actor): return
    try: DestinationAccessLevel((message.text or "").strip())
    except ValueError: await message.answer(texts.INVALID_INPUT); return
    await state.update_data(access_level=message.text.strip()); await state.set_state(AdminDestinationEdit.daily_limit); await message.answer(texts.ASK_DAILY_LIMIT)
@router.message(AdminDestinationEdit.daily_limit)
async def edit_limit(message:Message,actor:Actor,state:FSMContext,destination_service):
    from datetime import datetime
    if not await require_admin(message,actor): return
    try: value=int(message.text or ""); assert 0<value<=2**63-1
    except (ValueError,AssertionError): await message.answer(texts.INVALID_INPUT); return
    data=await state.get_data(); await state.update_data(daily_limit=value); await state.set_state(AdminDestinationEdit.review)
    await message.answer(texts.CONFIRM_METADATA,reply_markup=markup([[button(texts.BUTTON_CONFIRM,cb("nav","confirm","edit"))]]))
