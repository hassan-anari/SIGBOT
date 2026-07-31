from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
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
from chainura.telegram.routers import show_menu, show_status
from chainura.telegram.states import (AdminDestinationCreate, AdminDestinationEdit,
    AdminDestinationPriority, AdminSignalistArchive, AdminSignalistCreate)
from chainura.telegram.destination_inspector import (DestinationInspectionError,
    InspectionError, normalize_chat_id)

router = Router(name="phase4-admin-management")
FLOW_STATES = {
    "slc": AdminSignalistCreate,
    "dsc": AdminDestinationCreate,
    "dse": AdminDestinationEdit,
    "dsp": AdminDestinationPriority,
}
TOP_LEVEL = {texts.MENU_MAIN, texts.MENU_ADMIN_SIGNALISTS, texts.MENU_ADMIN_DESTINATIONS,
             texts.MENU_ADMIN_STATUS, texts.MENU_HELP}
EXCHANGE_FROM_CODE = {"o": "ourbit", "t": "toobit", "l": "lbank"}
CODE_FROM_EXCHANGE = {value: key for key, value in EXCHANGE_FROM_CODE.items()}


class FormInput(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        value = message.text or ""
        return bool(value) and not value.startswith("/") and value not in TOP_LEVEL


def cb(namespace: str, action: str, *args: object) -> str:
    return Phase4Callback(namespace, action, tuple(str(arg) for arg in args)).encode()


def button(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=data)


def markup(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def controls(flow: str, back_step: str | None = None) -> InlineKeyboardMarkup:
    row = []
    if back_step:
        row.append(button(texts.BUTTON_BACK_STEP, cb("nav", "back", flow, back_step)))
    row.append(button(texts.BUTTON_CANCEL, cb("nav", "cancel", flow)))
    return markup([row])


def candidate_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return markup([[button(texts.BUTTON_CONFIRM_DESTINATION, cb("nav", "candidate-confirm", chat_id))],
                   [button(texts.BUTTON_REENTER_CHAT, cb("nav", "candidate-retry", chat_id)),
                    button(texts.BUTTON_CANCEL, cb("nav", "cancel", "dsc"))]])


def destination_access_keyboard() -> InlineKeyboardMarkup:
    return markup([
        [button(texts.ACCESS_PUBLIC, cb("nav", "access", "public")),
         button(texts.ACCESS_PRIVATE, cb("nav", "access", "private"))],
        [button(texts.BUTTON_BACK_STEP, cb("nav", "back", "dsc", "candidate")),
         button(texts.BUTTON_CANCEL, cb("nav", "cancel", "dsc"))],
    ])


async def require_admin(event, actor: Actor, ack: CallbackAcknowledger | None = None) -> bool:
    if actor.kind is ActorKind.MAIN_ADMIN:
        return True
    denial = {ActorKind.ACTIVE_SIGNALIST: texts.UNKNOWN,
              ActorKind.INACTIVE_SIGNALIST: texts.INACTIVE,
              ActorKind.ARCHIVED_SIGNALIST: texts.ARCHIVED,
              ActorKind.UNKNOWN: texts.UNKNOWN}[actor.kind]
    if ack:
        await ack.answer_once(denial)
    target = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(target, Message):
        await target.answer(denial, reply_markup=remove_menu())
    return False


async def expired(message: Message) -> None:
    await message.answer(texts.FLOW_EXPIRED)


async def state_matches(state: FSMContext, expected, required: tuple[str, ...] = ()) -> dict | None:
    if await state.get_state() != expected.state:
        return None
    data = await state.get_data()
    return data if all(key in data for key in required) else None


def list_keyboard(kind, items, page, filter_code, include_archive=False):
    rows = [[button(texts.signalist_line(x) if kind == "sl" else texts.destination_line(x),
                    cb(kind, "view", x.id))] for x in items]
    filters = [button(texts.BUTTON_ALL, cb(kind, "list", "a", 1)),
               button(texts.BUTTON_ACTIVE, cb(kind, "list", "v", 1)),
               button(texts.BUTTON_INACTIVE, cb(kind, "list", "i", 1))]
    if include_archive:
        filters.append(button(texts.BUTTON_ARCHIVED, cb(kind, "list", "r", 1)))
    rows.append(filters)
    nav = []
    if page.page > 1:
        nav.append(button(texts.BUTTON_PREVIOUS, cb(kind, "list", filter_code, page.page - 1)))
    if page.page < page.pages:
        nav.append(button(texts.BUTTON_NEXT, cb(kind, "list", filter_code, page.page + 1)))
    if nav:
        rows.append(nav)
    rows.append([button(texts.BUTTON_ADD, cb(kind, "create"))])
    return markup(rows)


async def show_signalists(message, service, filter_code="a", page_no=1):
    statuses = {"a": None, "v": SignalistStatus.ACTIVE, "i": SignalistStatus.INACTIVE,
                "r": SignalistStatus.ARCHIVED}
    page = await service.list_page(statuses[filter_code], PageRequest(page_no))
    labels={"a":"همه","v":"🟢 فعال","i":"🟡 غیرفعال","r":"🔴 بایگانی‌شده"}
    content=texts.page_title(texts.SIGNALIST_LIST_TITLE, page.page, page.pages, labels[filter_code])
    if not page.items: content += "\n\n" + texts.EMPTY_LIST
    await message.answer(content,
                         reply_markup=list_keyboard("sl", page.items, page, filter_code, True))


async def show_destinations(message, service, filter_code="a", page_no=1):
    statuses = {"a": None, "v": DestinationStatus.ACTIVE, "i": DestinationStatus.INACTIVE}
    page = await service.list_page(statuses[filter_code], PageRequest(page_no))
    labels={"a":"همه","v":"🟢 فعال","i":"🟡 غیرفعال"}
    content=texts.page_title(texts.DESTINATION_LIST_TITLE, page.page, page.pages, labels[filter_code])
    if not page.items: content += "\n\n" + texts.EMPTY_LIST
    await message.answer(content,
                         reply_markup=list_keyboard("ds", page.items, page, filter_code))


async def show_signalist_detail(message, signalist_id, signalist_service, admin_signalist_service):
    item = await signalist_service.get_by_id(signalist_id)
    count = await admin_signalist_service.assignment_count(item.id)
    rows = [[button(texts.BUTTON_ASSIGNED_DESTINATIONS, cb("sl", "destinations", item.id, 1))]]
    if item.status is SignalistStatus.ACTIVE:
        rows.append([button(texts.BUTTON_DEACTIVATE, cb("sl", "status", item.id, "deactivate")),
                     button(texts.BUTTON_ARCHIVE, cb("sl", "status", item.id, "archive-request"))])
    elif item.status is SignalistStatus.INACTIVE:
        rows.append([button(texts.BUTTON_ACTIVATE, cb("sl", "status", item.id, "activate")),
                     button(texts.BUTTON_ARCHIVE, cb("sl", "status", item.id, "archive-request"))])
    await message.answer(texts.signalist_detail(item, count), reply_markup=markup(rows))


async def show_destination_detail(message, destination_id, destination_service):
    detail = await destination_service.detail(destination_id)
    destination = detail.destination
    action = "activate" if destination.status is DestinationStatus.INACTIVE else "deactivate"
    label = texts.BUTTON_ACTIVATE if action == "activate" else texts.BUTTON_DEACTIVATE
    rows = [[button(label, cb("ds", "status", destination.id, action))],
            [button(texts.BUTTON_EDIT, cb("ds", "edit", destination.id)),
             button(texts.BUTTON_PRIORITY, cb("ds", "priority", destination.id))],
            [button(texts.BUTTON_ASSIGNMENTS, cb("as", "list", destination.id, "assigned", 1))]]
    await message.answer(texts.destination_detail(detail), reply_markup=markup(rows))


# These global handlers are deliberately registered before every state-input handler.
@router.message(Command("start"))
async def global_start(message: Message, actor: Actor, state: FSMContext):
    await state.clear()
    await show_menu(message, actor, welcome=True)


@router.message(Command("help"))
@router.message(F.text == texts.MENU_HELP)
async def global_help(message: Message, actor: Actor):
    if actor.kind is ActorKind.MAIN_ADMIN:
        await message.answer(texts.HELP_ADMIN, reply_markup=admin_menu())
    elif actor.kind is ActorKind.ACTIVE_SIGNALIST:
        from chainura.telegram.presentation.keyboards import signalist_menu
        await message.answer(texts.HELP_SIGNALIST, reply_markup=signalist_menu())
    else:
        await require_admin(message, actor)


@router.message(F.text == texts.MENU_MAIN)
async def global_main(message: Message, actor: Actor, state: FSMContext):
    await state.clear()
    await show_menu(message, actor)


@router.message(F.text == texts.MENU_ADMIN_STATUS)
async def global_status(message: Message, actor: Actor, state: FSMContext):
    await state.clear()
    await show_status(message, actor)


@router.message(F.text == texts.MENU_ADMIN_SIGNALISTS)
async def signalists_menu(message: Message, actor: Actor, state: FSMContext, signalist_service):
    if not await require_admin(message, actor): return
    await state.clear()
    await show_signalists(message, signalist_service)


@router.message(F.text == texts.MENU_ADMIN_DESTINATIONS)
async def destinations_menu(message: Message, actor: Actor, state: FSMContext, destination_service):
    if not await require_admin(message, actor): return
    await state.clear()
    await show_destinations(message, destination_service)


def priority_keyboard(selected, *, destination_id: int | None = None):
    rows = []
    for index, value in enumerate(selected):
        code = CODE_FROM_EXCHANGE[value]
        if destination_id is None:
            rows.append([button(texts.BUTTON_UP, cb("nav", "exchange", "up", code)),
                         button(texts.EXCHANGE_NAMES[value], cb("nav", "exchange", "remove", code)),
                         button(texts.BUTTON_DOWN, cb("nav", "exchange", "down", code))])
        else:
            rows.append([button(texts.BUTTON_UP, cb("dp", "move", destination_id, code, "u")),
                         button(texts.EXCHANGE_NAMES[value], cb("dp", "remove", destination_id, code)),
                         button(texts.BUTTON_DOWN, cb("dp", "move", destination_id, code, "d"))])
    for value, code in CODE_FROM_EXCHANGE.items():
        if value not in selected:
            data = cb("nav", "exchange", "add", code) if destination_id is None else cb("dp", "add", destination_id, code)
            rows.append([button(texts.ADD_EXCHANGE.format(exchange=texts.EXCHANGE_NAMES[value]), data)])
    if selected:
        data = cb("nav", "back", "dsc", "review") if destination_id is None else cb("dp", "review", destination_id)
        rows.append([button(texts.BUTTON_CONFIRM, data)])
    flow = "dsc" if destination_id is None else "dsp"
    rows.append([button(texts.BUTTON_BACK_STEP, cb("nav", "back", flow, "limit" if destination_id is None else "detail")),
                 button(texts.BUTTON_CANCEL, cb("nav", "cancel", flow))])
    return markup(rows)


async def handle_navigation(query, parsed, state, destination_service, signalist_service,
                            destination_inspector=None):
    action, args = parsed.action, parsed.arguments
    if action == "cancel":
        flow = args[0]
        group = FLOW_STATES[flow]
        current = await state.get_state()
        if current is None or not current.startswith(group.__name__ + ":"):
            await expired(query.message); return True
        data = await state.get_data(); await state.clear()
        if flow == "slc": await show_signalists(query.message, signalist_service)
        elif flow in {"dsc"}: await show_destinations(query.message, destination_service)
        elif data.get("destination_id"): await show_destination_detail(query.message, data["destination_id"], destination_service)
        else: await query.message.answer(texts.FLOW_CANCELLED, reply_markup=admin_menu())
        return True
    if action in {"candidate-confirm", "candidate-retry"}:
        data = await state_matches(state, AdminDestinationCreate.candidate_review,
                                   ("internal_name", "telegram_chat_id", "destination_type"))
        chat_id=int(args[0])
        if data is None or data["telegram_chat_id"] != chat_id:
            await expired(query.message); return True
        if action == "candidate-retry":
            await state.set_data({}) if hasattr(state, "set_data") else await state.clear()
            await state.set_state(AdminDestinationCreate.telegram_chat_id)
            await query.message.answer(texts.ASK_DESTINATION_CHAT_ID, reply_markup=controls("dsc")); return True
        await state.set_state(AdminDestinationCreate.access_level)
        await query.message.answer(texts.ASK_DESTINATION_ACCESS, reply_markup=destination_access_keyboard()); return True
    if action == "access":
        data = await state_matches(state, AdminDestinationCreate.access_level,
                                   ("internal_name", "telegram_chat_id", "destination_type"))
        if data is None: await expired(query.message); return True
        await state.update_data(access_level=args[0]); await state.set_state(AdminDestinationCreate.daily_limit)
        await query.message.answer(texts.ASK_DAILY_LIMIT, reply_markup=controls("dsc", "access")); return True
    if action == "exchange":
        data = await state_matches(state, AdminDestinationCreate.priority,
                                   ("internal_name", "telegram_chat_id", "destination_type", "access_level", "daily_limit", "exchanges"))
        if data is None: await expired(query.message); return True
        selected = list(data["exchanges"]); operation, code = args; value = EXCHANGE_FROM_CODE[code]
        if operation == "add" and value not in selected: selected.append(value)
        elif operation == "remove" and value in selected: selected.remove(value)
        elif operation in {"up", "down"} and value in selected:
            old = selected.index(value); new = old + (-1 if operation == "up" else 1)
            if 0 <= new < len(selected): selected[old], selected[new] = selected[new], selected[old]
        await state.update_data(exchanges=selected)
        await query.message.answer(texts.priority_current(selected), reply_markup=priority_keyboard(selected)); return True
    if action == "confirm":
        flow = args[0]
        requirements = {
            "slc": (AdminSignalistCreate.review, ("telegram_user_id", "display_name")),
            "dsc": (AdminDestinationCreate.review, ("internal_name", "telegram_chat_id", "destination_type", "access_level", "daily_limit", "exchanges")),
            "dse": (AdminDestinationEdit.review, ("destination_id", "expected_updated_at", "access_level", "daily_limit")),
        }
        if flow not in requirements: await expired(query.message); return True
        expected, keys = requirements[flow]; data = await state_matches(state, expected, keys)
        if data is None: await expired(query.message); return True
        try:
            if flow == "dsc":
                if destination_inspector is None: await query.message.answer(texts.TELEGRAM_TEMPORARY); return True
                try: inspection=await destination_inspector.inspect(data["telegram_chat_id"])
                except DestinationInspectionError as exc:
                    messages={InspectionError.NOT_FOUND:texts.DESTINATION_NOT_FOUND, InspectionError.PRIVATE:texts.DESTINATION_PRIVATE,
                              InspectionError.TEMPORARY:texts.TELEGRAM_TEMPORARY}
                    await query.message.answer(messages[exc.reason]); return True
                if not inspection.bot_is_admin:
                    await query.message.answer(texts.DESTINATION_NOT_ADMIN); return True
                if inspection.title != data["internal_name"] or inspection.destination_type.value != data["destination_type"]:
                    await query.message.answer(texts.DESTINATION_NOT_FOUND); return True
                await destination_service.create(data["internal_name"], data["telegram_chat_id"],
                    DestinationType(data["destination_type"]), DestinationAccessLevel(data["access_level"]),
                    data["daily_limit"], tuple(Exchange(x) for x in data["exchanges"]))
            elif flow == "dse":
                await destination_service.update_configuration(data["destination_id"], DestinationAccessLevel(data["access_level"]),
                    data["daily_limit"], datetime.fromisoformat(data["expected_updated_at"]))
        except DuplicateDestinationChatIdError:
            await state.clear(); await query.message.answer(texts.DUPLICATE_DESTINATION_CHAT); return True
        except DuplicateDestinationInternalNameError:
            destination_id = data.get("destination_id")
            await state.clear(); await query.message.answer(texts.DUPLICATE_DESTINATION_NAME)
            if flow == "dse" and destination_id:
                await show_destination_detail(query.message, destination_id, destination_service)
            return True
        except ApplicationError:
            destination_id = data.get("destination_id")
            await state.clear(); await query.message.answer(texts.STALE_ACTION)
            if destination_id:
                await show_destination_detail(query.message, destination_id, destination_service)
            return True
        if flow != "slc":
            await state.clear(); await query.message.answer(texts.CREATED_SUCCESS); return True
        return False
    if action == "back":
        flow, step = args
        current = await state.get_state()
        group = FLOW_STATES[flow]
        if current is None or not current.startswith(group.__name__ + ":"):
            await expired(query.message); return True
        data = await state.get_data()
        if flow == "slc":
            targets = {"id": (AdminSignalistCreate.telegram_user_id, texts.ASK_SIGNALIST_ID),
                       "name": (AdminSignalistCreate.display_name, texts.ASK_SIGNALIST_NAME)}
        elif flow == "dsc":
            targets = {"chat": (AdminDestinationCreate.telegram_chat_id, texts.ASK_DESTINATION_CHAT_ID),
                       "candidate": (AdminDestinationCreate.candidate_review, ""),
                       "access": (AdminDestinationCreate.access_level, texts.ASK_DESTINATION_ACCESS),
                       "limit": (AdminDestinationCreate.daily_limit, texts.ASK_DAILY_LIMIT),
                       "priority": (AdminDestinationCreate.priority, texts.ASK_PRIORITY)}
        elif flow == "dse":
            targets = {"access": (AdminDestinationEdit.access_level, texts.ASK_DESTINATION_ACCESS_TEXT),
                       "limit": (AdminDestinationEdit.daily_limit, texts.ASK_DAILY_LIMIT)}
        else:
            if step == "detail" and data.get("destination_id"):
                await state.clear(); await show_destination_detail(query.message, data["destination_id"], destination_service)
            else: await expired(query.message)
            return True
        if step == "review":
            required = ("internal_name", "telegram_chat_id", "destination_type", "access_level", "daily_limit", "exchanges")
            if flow != "dsc" or not all(key in data for key in required) or not data["exchanges"]:
                await expired(query.message); return True
            await state.set_state(AdminDestinationCreate.review)
            await query.message.answer(texts.destination_review(data), reply_markup=markup([[
                button(texts.BUTTON_CONFIRM, cb("nav", "confirm", "dsc")),
                button(texts.BUTTON_BACK_STEP, cb("nav", "back", "dsc", "priority")),
                button(texts.BUTTON_CANCEL, cb("nav", "cancel", "dsc"))]])); return True
        if step not in targets: await expired(query.message); return True
        target, prompt = targets[step]; await state.set_state(target)
        if flow == "dsc" and step == "candidate":
            if not all(k in data for k in ("internal_name","telegram_chat_id","destination_type")): await expired(query.message); return True
            await state.set_state(AdminDestinationCreate.candidate_review)
            await query.message.answer(texts.candidate_review(data),reply_markup=candidate_keyboard(data["telegram_chat_id"]))
        elif flow == "dsc" and step == "access":
            await query.message.answer(prompt, reply_markup=destination_access_keyboard())
        elif step == "priority": await query.message.answer(prompt, reply_markup=priority_keyboard(data.get("exchanges", [])))
        else: await query.message.answer(prompt, reply_markup=controls(flow))
        return True
    return False


@router.callback_query(F.data.startswith("p4:"))
async def callbacks(query: CallbackQuery, actor: Actor, callback_ack: CallbackAcknowledger,
                    state: FSMContext, signalist_service, destination_service,
                    assignment_service, admin_signalist_service, destination_inspector=None):
    parsed = parse_phase4(query.data)
    if parsed is None: await callback_ack.answer_once(texts.INVALID_CALLBACK); return
    if not await require_admin(query, actor, callback_ack): return
    await callback_ack.answer_once()
    if query.message is None: return
    if parsed.namespace == "nav":
        handled = await handle_navigation(query, parsed, state, destination_service, signalist_service, destination_inspector)
        if parsed.action == "confirm" and parsed.arguments == ("slc",) and not handled:
            data = await state_matches(state, AdminSignalistCreate.review,
                                       ("telegram_user_id", "display_name"))
            if data is None: await expired(query.message); return
            try: await admin_signalist_service.create(data["telegram_user_id"], data["display_name"], None)
            except DuplicateTelegramUserIdError: await state.clear(); await query.message.answer(texts.DUPLICATE_SIGNALIST); return
            except ApplicationError: await state.clear(); await query.message.answer(texts.INVALID_INPUT); return
            await state.clear(); await query.message.answer(texts.CREATED_SUCCESS)
        return
    args = parsed.arguments
    try:
        if parsed.namespace == "sl" and parsed.action == "list": await show_signalists(query.message, signalist_service, args[0], int(args[1]))
        elif parsed.namespace == "sl" and parsed.action == "create":
            await state.clear(); await state.set_state(AdminSignalistCreate.telegram_user_id)
            await query.message.answer(texts.ASK_SIGNALIST_ID, reply_markup=controls("slc"))
        elif parsed.namespace == "sl" and parsed.action == "view":
            if await state.get_state() == AdminSignalistArchive.confirming.state: await state.clear()
            await show_signalist_detail(query.message, int(args[0]), signalist_service, admin_signalist_service)
        elif parsed.namespace == "sl" and parsed.action == "destinations":
            sid, requested = int(args[0]), int(args[1]); page = await admin_signalist_service.assigned_destinations(sid, PageRequest(requested))
            body = "\n".join(texts.destination_line(item) for item in page.items) or texts.EMPTY_LIST
            rows = []; nav = []
            if page.page > 1: nav.append(button(texts.BUTTON_PREVIOUS, cb("sl", "destinations", sid, page.page - 1)))
            if page.page < page.pages: nav.append(button(texts.BUTTON_NEXT, cb("sl", "destinations", sid, page.page + 1)))
            if nav: rows.append(nav)
            rows.append([button(texts.BUTTON_BACK_STEP, cb("sl", "view", sid))])
            await query.message.answer(texts.page_title(texts.ASSIGNED_DESTINATIONS_TITLE, page.page, page.pages) + "\n" + body, reply_markup=markup(rows))
        elif parsed.namespace == "sl" and parsed.action == "status":
            sid, action = int(args[0]), args[1]
            if action == "archive-request":
                current = await signalist_service.get_by_id(sid)
                if current.status is SignalistStatus.ARCHIVED: await query.message.answer(texts.STALE_ACTION); return
                await state.clear(); await state.set_state(AdminSignalistArchive.confirming); await state.update_data(signalist_id=sid)
                await query.message.answer(texts.archive_confirmation(current.display_name), reply_markup=markup([[
                    button(texts.CONFIRM_ARCHIVE_BUTTON, cb("sl", "status", sid, "archive-confirm")),
                    button(texts.BUTTON_BACK_STEP, cb("sl", "view", sid))]])); return
            if action == "archive-confirm":
                data = await state_matches(state, AdminSignalistArchive.confirming, ("signalist_id",))
                if data is None or data["signalist_id"] != sid: await expired(query.message); return
                current = await signalist_service.get_by_id(sid)
                if current.status is SignalistStatus.ARCHIVED: await state.clear(); await query.message.answer(texts.STALE_ACTION); return
                await signalist_service.archive(sid); await state.clear(); await show_signalist_detail(query.message, sid, signalist_service, admin_signalist_service); return
            await (signalist_service.activate if action == "activate" else signalist_service.deactivate)(sid)
            await show_signalist_detail(query.message, sid, signalist_service, admin_signalist_service)
        elif parsed.namespace == "ds" and parsed.action == "list": await show_destinations(query.message, destination_service, args[0], int(args[1]))
        elif parsed.namespace == "ds" and parsed.action == "create":
            await state.clear(); await state.set_state(AdminDestinationCreate.telegram_chat_id)
            await query.message.answer(texts.ASK_DESTINATION_CHAT_ID, reply_markup=controls("dsc"))
        elif parsed.namespace == "ds" and parsed.action == "view": await show_destination_detail(query.message, int(args[0]), destination_service)
        elif parsed.namespace == "ds" and parsed.action == "status":
            did, action = int(args[0]), args[1]
            await (destination_service.activate if action == "activate" else destination_service.deactivate)(did)
            await show_destination_detail(query.message, did, destination_service)
        elif parsed.namespace == "ds" and parsed.action == "edit":
            destination = await destination_service.get(int(args[0])); await state.clear()
            await state.update_data(destination_id=destination.id, expected_updated_at=destination.updated_at.isoformat())
            await state.set_state(AdminDestinationEdit.access_level)
            await query.message.answer(texts.ASK_DESTINATION_ACCESS_TEXT, reply_markup=controls("dse"))
        elif parsed.namespace == "ds" and parsed.action == "priority":
            detail = await destination_service.detail(int(args[0])); selected = [x.value for x in detail.priority.exchanges]
            await state.clear(); await state.update_data(destination_id=detail.destination.id,
                expected_updated_at=detail.destination.updated_at.isoformat(), exchanges=selected)
            await state.set_state(AdminDestinationPriority.editing)
            await query.message.answer(texts.EDIT_PRIORITY, reply_markup=priority_keyboard(selected, destination_id=detail.destination.id))
        elif parsed.namespace == "dp":
            did = int(args[0]); data = await state_matches(state,
                AdminDestinationPriority.review if parsed.action in {"confirm", "back"} else AdminDestinationPriority.editing,
                ("destination_id", "expected_updated_at", "exchanges"))
            if data is None or data["destination_id"] != did: await expired(query.message); return
            selected = list(data["exchanges"])
            if parsed.action in {"add", "remove", "move"}:
                value = EXCHANGE_FROM_CODE[args[1]]
                if parsed.action == "add" and value not in selected: selected.append(value)
                elif parsed.action == "remove" and value in selected: selected.remove(value)
                elif parsed.action == "move" and value in selected:
                    old = selected.index(value); new = old + (-1 if args[2] == "u" else 1)
                    if 0 <= new < len(selected): selected[old], selected[new] = selected[new], selected[old]
                await state.update_data(exchanges=selected)
                await query.message.answer(texts.priority_current(selected), reply_markup=priority_keyboard(selected, destination_id=did))
            elif parsed.action == "review":
                if not selected: await query.message.answer(texts.INVALID_INPUT); return
                await state.set_state(AdminDestinationPriority.review)
                await query.message.answer(texts.priority_current(selected), reply_markup=markup([[
                    button(texts.BUTTON_CONFIRM, cb("dp", "confirm", did)),
                    button(texts.BUTTON_BACK_STEP, cb("dp", "back", did)),
                    button(texts.BUTTON_CANCEL, cb("nav", "cancel", "dsp"))]]))
            elif parsed.action == "back":
                await state.set_state(AdminDestinationPriority.editing)
                await query.message.answer(texts.EDIT_PRIORITY,
                    reply_markup=priority_keyboard(selected, destination_id=did))
            else:
                try:
                    await destination_service.replace_priority(did, tuple(Exchange(x) for x in selected), datetime.fromisoformat(data["expected_updated_at"]))
                except ApplicationError:
                    await state.clear(); await query.message.answer(texts.STALE_ACTION)
                    await show_destination_detail(query.message, did, destination_service); return
                await state.clear(); await show_destination_detail(query.message, did, destination_service)
        elif parsed.namespace == "as" and parsed.action == "list":
            did, mode, requested = int(args[0]), args[1], int(args[2])
            page = await (assignment_service.list_assigned if mode == "assigned" else assignment_service.list_eligible)(did, PageRequest(requested))
            body = "\n".join(texts.signalist_line(item) for item in page.items) or texts.EMPTY_LIST
            rows = [[button(texts.safe(item.display_name), cb("as", "remove" if mode == "assigned" else "add", did, item.id))] for item in page.items]
            nav = []
            if page.page > 1: nav.append(button(texts.BUTTON_PREVIOUS, cb("as", "list", did, mode, page.page - 1)))
            if page.page < page.pages: nav.append(button(texts.BUTTON_NEXT, cb("as", "list", did, mode, page.page + 1)))
            if nav: rows.append(nav)
            rows.append([button(texts.SHOW_ELIGIBLE if mode == "assigned" else texts.SHOW_ASSIGNED,
                                cb("as", "list", did, "eligible" if mode == "assigned" else "assigned", 1)),
                         button(texts.BUTTON_BACK_STEP, cb("ds", "view", did))])
            await query.message.answer(texts.page_title(texts.SIGNALIST_LIST_TITLE, page.page, page.pages) + "\n" + body, reply_markup=markup(rows))
        elif parsed.namespace == "as" and parsed.action == "add":
            await assignment_service.assign(int(args[0]), int(args[1])); await query.message.answer(texts.CREATED_SUCCESS)
        elif parsed.namespace == "as" and parsed.action == "remove":
            await query.message.answer(texts.CONFIRM_UNASSIGN, reply_markup=markup([[
                button(texts.BUTTON_CONFIRM, cb("as", "confirm", "remove", args[0], args[1])),
                button(texts.BUTTON_BACK_STEP, cb("as", "list", args[0], "assigned", 1))]]))
        elif parsed.namespace == "as" and parsed.action == "confirm":
            await assignment_service.unassign(int(args[1]), int(args[2])); await query.message.answer(texts.CREATED_SUCCESS)
    except DuplicateDestinationAssignmentError: await query.message.answer(texts.ASSIGNMENT_DUPLICATE)
    except DestinationAssignmentNotFoundError: await query.message.answer(texts.ASSIGNMENT_MISSING)
    except SignalistNotEligibleForAssignmentError: await query.message.answer(texts.ASSIGNMENT_INELIGIBLE)
    except ApplicationError: await query.message.answer(texts.STALE_ACTION)


@router.message(AdminSignalistCreate.telegram_user_id, FormInput())
async def sl_id(message: Message, actor: Actor, state: FSMContext):
    if not await require_admin(message, actor): return
    try: value = int(message.text); assert str(value) == message.text.strip() and 0 < value <= 2**63 - 1
    except (ValueError, AssertionError): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(telegram_user_id=value); await state.set_state(AdminSignalistCreate.display_name)
    await message.answer(texts.ASK_SIGNALIST_NAME, reply_markup=controls("slc", "id"))


@router.message(AdminSignalistCreate.display_name, FormInput())
async def sl_name(message: Message, actor: Actor, state: FSMContext):
    if not await require_admin(message, actor): return
    if not message.text.strip(): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(display_name=message.text.strip()); data=await state.get_data()
    await state.set_state(AdminSignalistCreate.review)
    await message.answer(texts.signalist_review(data), reply_markup=markup([[
        button(texts.BUTTON_CONFIRM, cb("nav", "confirm", "slc")),
        button(texts.BUTTON_BACK_STEP, cb("nav", "back", "slc", "name")),
        button(texts.BUTTON_CANCEL, cb("nav", "cancel", "slc"))]]))


@router.message(AdminDestinationCreate.telegram_chat_id, FormInput())
async def ds_chat(message: Message, actor: Actor, state: FSMContext, destination_inspector):
    if not await require_admin(message, actor): return
    try: value = normalize_chat_id(message.text)
    except ValueError: await message.answer(texts.INVALID_CHAT_ID); return
    await state.set_state(AdminDestinationCreate.discovering)
    try: inspection=await destination_inspector.inspect(value)
    except DestinationInspectionError as exc:
        await state.set_state(AdminDestinationCreate.telegram_chat_id)
        messages={InspectionError.NOT_FOUND:texts.DESTINATION_NOT_FOUND, InspectionError.PRIVATE:texts.DESTINATION_PRIVATE, InspectionError.TEMPORARY:texts.TELEGRAM_TEMPORARY}
        await message.answer(messages[exc.reason]); return
    if not inspection.bot_is_admin:
        await state.set_state(AdminDestinationCreate.telegram_chat_id); await message.answer(texts.DESTINATION_NOT_ADMIN); return
    await state.update_data(telegram_chat_id=value,internal_name=inspection.title,destination_type=inspection.destination_type.value)
    data=await state.get_data(); await state.set_state(AdminDestinationCreate.candidate_review)
    await message.answer(texts.candidate_review(data),reply_markup=candidate_keyboard(value))


@router.message(AdminDestinationCreate.daily_limit, FormInput())
async def ds_limit(message: Message, actor: Actor, state: FSMContext):
    if not await require_admin(message, actor): return
    try: value = int(message.text); assert str(value) == message.text.strip() and 0 < value <= 2**63 - 1
    except (ValueError, AssertionError): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(daily_limit=value, exchanges=[]); await state.set_state(AdminDestinationCreate.priority)
    await message.answer(texts.ASK_PRIORITY, reply_markup=priority_keyboard([]))


@router.message(AdminDestinationEdit.access_level, FormInput())
async def edit_access(message: Message, actor: Actor, state: FSMContext):
    if not await require_admin(message, actor): return
    value = {texts.ACCESS_PUBLIC: "public", texts.ACCESS_PRIVATE: "private"}.get(message.text.strip())
    if value is None: await message.answer(texts.INVALID_INPUT); return
    await state.update_data(access_level=value); await state.set_state(AdminDestinationEdit.daily_limit)
    await message.answer(texts.ASK_DAILY_LIMIT, reply_markup=controls("dse", "access"))


@router.message(AdminDestinationEdit.daily_limit, FormInput())
async def edit_limit(message: Message, actor: Actor, state: FSMContext):
    if not await require_admin(message, actor): return
    try: value = int(message.text); assert str(value) == message.text.strip() and 0 < value <= 2**63 - 1
    except (ValueError, AssertionError): await message.answer(texts.INVALID_INPUT); return
    await state.update_data(daily_limit=value); data = await state.get_data()
    required = ("destination_id", "expected_updated_at", "access_level", "daily_limit")
    if not all(key in data for key in required): await expired(message); return
    await state.set_state(AdminDestinationEdit.review)
    await message.answer(texts.metadata_review(data), reply_markup=markup([[
        button(texts.BUTTON_CONFIRM, cb("nav", "confirm", "dse")),
        button(texts.BUTTON_BACK_STEP, cb("nav", "back", "dse", "limit")),
        button(texts.BUTTON_CANCEL, cb("nav", "cancel", "dse"))]]))
