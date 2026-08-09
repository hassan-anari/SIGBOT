from secrets import token_urlsafe
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from chainura.application.access import Actor, ActorKind
from chainura.application.pagination import PageRequest
from chainura.domain.message_template import DestinationMessageKind
from chainura.telegram.middlewares import CallbackAcknowledger
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import admin_menu
from chainura.telegram.states import AdminMessageTemplateManagement

router = Router(name="phase7-message-template-management")
KINDS = {"i":DestinationMessageKind.INITIAL_PHOTO_CAPTION,"d":DestinationMessageKind.SIGNAL_DETAILS,
         "t":DestinationMessageKind.TP_HIT,"s":DestinationMessageKind.SL_HIT,
         "b":DestinationMessageKind.BREAKEVEN,"c":DestinationMessageKind.SIGNAL_CANCELLED}
LABELS = texts.MESSAGE_TEMPLATE_KIND_LABELS
SOURCES = texts.MESSAGE_TEMPLATE_SOURCE_LABELS
def kb(rows): return InlineKeyboardMarkup(inline_keyboard=rows)
def btn(label,data): return InlineKeyboardButton(text=label,callback_data=data)
def cb(token,*parts): return ":".join(("mt",token,*map(str,parts)))

async def menu(message,state):
    token=token_urlsafe(6); await state.clear(); await state.update_data(mt_token=token); await state.set_state(AdminMessageTemplateManagement.browsing)
    await message.answer(texts.MESSAGE_TEMPLATE_MENU,reply_markup=kb([[btn(LABELS[c],cb(token,"kind",c))] for c in KINDS]+[[btn(texts.BUTTON_CANCEL,cb(token,"cancel"))]]))

async def panel(message,state,service,token):
    data=await state.get_data();kind=KINDS[data["kind"]];destination_id=data.get("destination_id")
    template,source=await service.effective_template(kind,destination_id)
    scope=(texts.MESSAGE_TEMPLATE_SCOPE_GLOBAL if destination_id is None else
           texts.MESSAGE_TEMPLATE_SCOPE_DESTINATION.format(destination=texts.safe(data['destination_name'])))
    rows=[[btn(texts.MESSAGE_TEMPLATE_VIEW_BUTTON,cb(token,"view"))],[btn(texts.MESSAGE_TEMPLATE_EDIT_BUTTON,cb(token,"edit"))],[btn(texts.MESSAGE_TEMPLATE_REMOVE_BUTTON,cb(token,"remove"))]]
    if destination_id is None:rows.append([btn(texts.MESSAGE_TEMPLATE_DESTINATION_BUTTON,cb(token,"dest",1))])
    rows.append([btn(texts.BUTTON_BACK_STEP,cb(token,"back")),btn(texts.BUTTON_CANCEL,cb(token,"cancel"))])
    await message.answer(texts.MESSAGE_TEMPLATE_STATUS.format(kind=LABELS[data['kind']],scope=scope,source=SOURCES[source.value]),reply_markup=kb(rows))

@router.message(F.text==texts.MENU_ADMIN_MESSAGE_TEMPLATES)
async def open_menu(message:Message,actor:Actor,state:FSMContext):
    if actor.kind is not ActorKind.MAIN_ADMIN: await message.answer(texts.UNKNOWN); return
    await menu(message,state)

@router.callback_query(F.data.startswith("mt:"))
async def callbacks(query:CallbackQuery,actor:Actor,callback_ack:CallbackAcknowledger,state:FSMContext,message_template_service,destination_service):
    if actor.kind is not ActorKind.MAIN_ADMIN: await callback_ack.answer_once(texts.UNKNOWN); return
    await callback_ack.answer_once(); parts=(query.data or "").split(":")
    if len(parts)<3 or query.message is None: return
    token,action,*args=parts[1:]; data=await state.get_data()
    if data.get("mt_token")!=token: await query.message.answer(texts.FLOW_EXPIRED); return
    if action=="cancel": await state.clear(); await query.message.answer(texts.FLOW_CANCELLED,reply_markup=admin_menu()); return
    if action=="back": await menu(query.message,state); return
    if action=="kind" and len(args)==1 and args[0] in KINDS:
        await state.update_data(kind=args[0],destination_id=None,destination_name=None); await panel(query.message,state,message_template_service,token); return
    if "kind" not in data: await query.message.answer(texts.FLOW_EXPIRED); return
    kind=KINDS[data["kind"]]
    if action=="view":
        template,source=await message_template_service.effective_template(kind,data.get("destination_id"));await query.message.answer(texts.MESSAGE_TEMPLATE_VIEW.format(source=SOURCES[source.value],template=template));return
    if action=="edit": await state.set_state(AdminMessageTemplateManagement.editing);await query.message.answer(texts.MESSAGE_TEMPLATE_SEND);return
    if action=="remove":
        if data.get("destination_id") is None:await message_template_service.remove_global(kind)
        else:await message_template_service.remove_destination(kind,data["destination_id"])
        await query.message.answer(texts.MESSAGE_TEMPLATE_REMOVED);await panel(query.message,state,message_template_service,token);return
    if action=="dest":
        page=await destination_service.list_page(None,PageRequest(int(args[0])));rows=[[btn(texts.destination_button_label(d),cb(token,"pick",d.id))] for d in page.items]
        rows.append([btn(texts.BUTTON_BACK_STEP,cb(token,"kind",data["kind"]))]);await query.message.answer(texts.MESSAGE_TEMPLATE_SELECT_DESTINATION,reply_markup=kb(rows));return
    if action=="pick":
        destination=await destination_service.get(int(args[0]));await state.update_data(destination_id=destination.id,destination_name=destination.internal_name);await panel(query.message,state,message_template_service,token);return
    if action=="confirm" and await state.get_state()==AdminMessageTemplateManagement.confirming.state:
        text=data.get("pending_text")
        if data.get("destination_id") is None:await message_template_service.set_global(kind,text)
        else:await message_template_service.set_destination(kind,data["destination_id"],text)
        await state.set_state(AdminMessageTemplateManagement.browsing);await query.message.answer(texts.MESSAGE_TEMPLATE_SAVED);await panel(query.message,state,message_template_service,token);return
    await query.message.answer(texts.INVALID_CALLBACK)

@router.message(AdminMessageTemplateManagement.editing)
async def receive_text(message:Message,actor:Actor,state:FSMContext,message_template_service):
    if actor.kind is not ActorKind.MAIN_ADMIN:return
    data=await state.get_data()
    try:preview=message_template_service.preview(KINDS[data["kind"]],message.text or "")
    except Exception:await message.answer(texts.MESSAGE_TEMPLATE_INVALID);return
    await state.update_data(pending_text=message.text);await state.set_state(AdminMessageTemplateManagement.confirming)
    await message.answer(texts.MESSAGE_TEMPLATE_PREVIEW.format(preview=preview),reply_markup=kb([[btn(texts.MESSAGE_TEMPLATE_CONFIRM_BUTTON,cb(data["mt_token"],"confirm"))],[btn(texts.MESSAGE_TEMPLATE_REEDIT_BUTTON,cb(data["mt_token"],"edit")),btn(texts.BUTTON_CANCEL,cb(data["mt_token"],"cancel"))]]))
