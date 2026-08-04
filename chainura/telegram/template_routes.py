from __future__ import annotations
from secrets import token_urlsafe
from aiogram import F,Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery,InlineKeyboardButton,InlineKeyboardMarkup,Message
from chainura.application.access import Actor,ActorKind
from chainura.application.pagination import PageRequest
from chainura.application.template_admin import download_png_document
from chainura.domain.destination import Exchange
from chainura.errors import ApplicationError
from chainura.telegram.middlewares import CallbackAcknowledger
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import admin_menu
from chainura.telegram.states import AdminTemplateManagement
router=Router(name="phase5c-template-management")
CODES={"o":Exchange.OURBIT,"t":Exchange.TOOBIT,"l":Exchange.LBANK}
NAMES={e:texts.EXCHANGE_NAMES[e.value] for e in Exchange}

def data(token,*parts):return ":".join(("tpl",token,*map(str,parts)))
def keyboard(rows):return InlineKeyboardMarkup(inline_keyboard=rows)
def button(label,callback):return InlineKeyboardButton(text=label,callback_data=callback)
def controls(token,back="menu"):return [button(texts.BUTTON_BACK_STEP,data(token,back)),button(texts.BUTTON_CANCEL,data(token,"cancel"))]
async def valid(state,expected,token,required=()):
 if await state.get_state()!=expected.state:return None
 current=await state.get_data()
 return current if current.get("template_token")==token and all(k in current for k in required) else None
async def deny(event,actor,ack=None):
 if actor.kind is ActorKind.MAIN_ADMIN:return False
 if ack:await ack.answer_once(texts.UNKNOWN)
 elif isinstance(event,Message):await event.answer(texts.UNKNOWN)
 return True
async def show_menu(message,state):
 token=token_urlsafe(6);await state.clear();await state.update_data(template_token=token);await state.set_state(AdminTemplateManagement.exchange)
 rows=[[button(NAMES[e],data(token,"exchange",code))] for code,e in CODES.items()]
 rows.append([button(texts.BUTTON_CANCEL,data(token,"cancel"))]);await message.answer(texts.TEMPLATE_MENU,reply_markup=keyboard(rows))
async def show_exchange(message,state,token,exchange,service):
 configured=await service.configured(exchange)
 await state.update_data(exchange=exchange.value);await state.set_state(AdminTemplateManagement.exchange)
 status=texts.TEMPLATE_CONFIGURED if configured else texts.TEMPLATE_MISSING
 rows=[[button(texts.TEMPLATE_UPLOAD_DEFAULT,data(token,"upload-default"))],
       [button(texts.TEMPLATE_SELECT_DESTINATION,data(token,"destinations",1))],controls(token)]
 await message.answer(texts.TEMPLATE_DEFAULT_STATUS.format(exchange=NAMES[exchange],status=status),reply_markup=keyboard(rows))
async def show_destinations(message,state,token,page_no,destination_service):
 page=await destination_service.list_page(None,PageRequest(page_no));await state.set_state(AdminTemplateManagement.destination)
 rows=[[button(texts.destination_button_label(item),data(token,"destination",item.id))] for item in page.items]
 nav=[]
 if page.page>1:nav.append(button(texts.BUTTON_PREVIOUS,data(token,"destinations",page.page-1)))
 if page.page<page.pages:nav.append(button(texts.BUTTON_NEXT,data(token,"destinations",page.page+1)))
 if nav:rows.append(nav)
 rows.append(controls(token,"exchange-back"));await message.answer(texts.DESTINATION_LIST_TITLE.format(page=page.page,pages=page.pages,filter=texts.TEMPLATE_ALL),reply_markup=keyboard(rows))

@router.message(F.text==texts.MENU_ADMIN_TEMPLATES)
async def template_menu(message:Message,actor:Actor,state:FSMContext):
 if await deny(message,actor):return
 await show_menu(message,state)

@router.callback_query(F.data.startswith("tpl:"))
async def template_callback(query:CallbackQuery,actor:Actor,callback_ack:CallbackAcknowledger,state:FSMContext,
 template_admin_service,destination_service):
 parts=(query.data or "").split(":")
 if len(parts)<3 or any(not p.isascii() for p in parts[:2]):await callback_ack.answer_once(texts.INVALID_CALLBACK);return
 token,action,*args=parts[1:]
 if await deny(query,actor,callback_ack):return
 await callback_ack.answer_once()
 if query.message is None:return
 current=await state.get_data()
 if current.get("template_token")!=token:await query.message.answer(texts.FLOW_EXPIRED);return
 try:
  if action=="cancel":await state.clear();await query.message.answer(texts.FLOW_CANCELLED,reply_markup=admin_menu());return
  if action=="menu":await show_menu(query.message,state);return
  if action=="exchange":
   exchange=CODES.get(args[0]) if len(args)==1 else None
   if exchange is None or await valid(state,AdminTemplateManagement.exchange,token) is None:raise ValueError
   await show_exchange(query.message,state,token,exchange,template_admin_service);return
  exchange=Exchange(current["exchange"])
  if action=="exchange-back":await show_exchange(query.message,state,token,exchange,template_admin_service);return
  if action=="upload-default":
   if await valid(state,AdminTemplateManagement.exchange,token,("exchange",)) is None:raise ValueError
   await state.set_state(AdminTemplateManagement.upload_default);await query.message.answer(texts.TEMPLATE_SEND_DOCUMENT);return
  if action=="destinations":await show_destinations(query.message,state,token,int(args[0]),destination_service);return
  if action=="destination":
   if await valid(state,AdminTemplateManagement.destination,token,("exchange",)) is None:raise ValueError
   destination=await destination_service.get(int(args[0]));configured=await template_admin_service.configured(exchange,destination_id=destination.id)
   await state.update_data(destination_id=destination.id,destination_name=destination.internal_name);await state.set_state(AdminTemplateManagement.override)
   status=texts.TEMPLATE_CONFIGURED if configured else texts.TEMPLATE_MISSING
   rows=[[button(texts.TEMPLATE_UPLOAD_OVERRIDE,data(token,"upload-override"))]]
   if configured:rows.append([button(texts.TEMPLATE_REMOVE_OVERRIDE,data(token,"remove"))])
   rows.append([button(texts.BUTTON_BACK_STEP,data(token,"destinations",1)),button(texts.BUTTON_CANCEL,data(token,"cancel"))]);await query.message.answer(texts.TEMPLATE_OVERRIDE_STATUS.format(destination=texts.safe(destination.internal_name),exchange=NAMES[exchange],status=status),reply_markup=keyboard(rows));return
  if action=="upload-override":
   if await valid(state,AdminTemplateManagement.override,token,("exchange","destination_id")) is None:raise ValueError
   await state.set_state(AdminTemplateManagement.upload_override);await query.message.answer(texts.TEMPLATE_SEND_DOCUMENT);return
  if action=="remove":
   if await valid(state,AdminTemplateManagement.override,token,("exchange","destination_id")) is None:raise ValueError
   await state.set_state(AdminTemplateManagement.remove_confirm);await query.message.answer(texts.TEMPLATE_REMOVE_CONFIRM,reply_markup=keyboard([[button(texts.BUTTON_CONFIRM,data(token,"remove-confirm"))],controls(token,"exchange-back")])) ;return
  if action=="remove-confirm":
   context=await valid(state,AdminTemplateManagement.remove_confirm,token,("exchange","destination_id"))
   if context is None:raise ValueError
   await template_admin_service.remove_override(exchange,context["destination_id"]);await state.clear();await query.message.answer(texts.TEMPLATE_REMOVED,reply_markup=admin_menu());return
  raise ValueError
 except (ValueError,KeyError,ApplicationError):await query.message.answer(texts.FLOW_EXPIRED)

@router.message(AdminTemplateManagement.upload_default)
@router.message(AdminTemplateManagement.upload_override)
async def template_upload(message:Message,actor:Actor,state:FSMContext,template_admin_service):
 if await deny(message,actor):return
 current=await state.get_data();state_name=await state.get_state()
 required=("template_token","exchange")
 if not all(key in current for key in required):await message.answer(texts.FLOW_EXPIRED);return
 if message.photo or message.document is None:await message.answer(texts.TEMPLATE_DOCUMENT_REQUIRED);return
 try:
  payload=await download_png_document(message.bot,message.document)
  destination_id=current.get("destination_id") if state_name==AdminTemplateManagement.upload_override.state else None
  await template_admin_service.install(Exchange(current["exchange"]),payload,destination_id=destination_id)
 except ApplicationError:await message.answer(texts.TEMPLATE_DOCUMENT_REQUIRED);return
 await state.clear();await message.answer(texts.TEMPLATE_INSTALLED,reply_markup=admin_menu())
