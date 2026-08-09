from aiogram import F,Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery,InlineKeyboardButton,InlineKeyboardMarkup,Message
from chainura.application.access import Actor,ActorKind
from chainura.application.pagination import PageRequest
from chainura.domain.signal import SignalLifecycleStatus,TradeDirection
from chainura.telegram.middlewares import CallbackAcknowledger
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.formatting import format_decimal,format_leverage,format_symbol
from chainura.telegram.presentation.keyboards import signalist_menu
from chainura.telegram.states import SignalCreate

router=Router(name="phase7-active-signals");PREFIX="as7:"
def cb(action,*parts):return PREFIX+":".join((action,*(str(x) for x in parts)))
def _parse(data):
 try:
  parts=data.split(":");return parts[1],tuple(int(x) for x in parts[2:])
 except (ValueError,IndexError,AttributeError):return None,()
def _label(x):return f"{x.symbol} • {'لانگ' if x.direction is TradeDirection.LONG else 'شورت'} • {texts.ACTIVE_SIGNAL_PENDING if x.status is SignalLifecycleStatus.PENDING_DETAILS else texts.ACTIVE_SIGNAL_ACTIVE}"
def list_keyboard(page):
 rows=[[InlineKeyboardButton(text=_label(x),callback_data=cb("d",x.id,page.page))] for x in page.items];nav=[]
 if page.page>1:nav.append(InlineKeyboardButton(text=texts.BUTTON_PREVIOUS,callback_data=cb("l",page.page-1)))
 if page.page<page.pages:nav.append(InlineKeyboardButton(text=texts.BUTTON_NEXT,callback_data=cb("l",page.page+1)))
 if nav:rows.append(nav)
 rows.append([InlineKeyboardButton(text=texts.BUTTON_BACK_STEP,callback_data=cb("m"))]);return InlineKeyboardMarkup(inline_keyboard=rows)
def detail_text(x):
 rows=["<b>📋 سیگنال فعال</b>","",f"نماد: {format_symbol(x.symbol)}",f"جهت: {'لانگ' if x.direction is TradeDirection.LONG else 'شورت'}",f"ورود: {format_decimal(x.entry)}",f"اهرم: {format_leverage(x.leverage)}",f"وضعیت: {texts.ACTIVE_SIGNAL_PENDING if x.status is SignalLifecycleStatus.PENDING_DETAILS else texts.ACTIVE_SIGNAL_ACTIVE}"]
 if x.status is SignalLifecycleStatus.ACTIVE:
  rows.extend([f"تارگت {i}: {format_decimal(v)}" for i,v in enumerate(x.take_profits,1)]);rows.append(f"حد ضرر: {format_decimal(x.stop_loss)}")
 return "\n".join(rows)
def detail_keyboard(x,page):
 rows=[]
 if x.status is SignalLifecycleStatus.ACTIVE:
  rows.extend([[InlineKeyboardButton(text=(texts.ACTIVE_SIGNAL_TP_REACHED if i<=x.highest_tp_index else texts.ACTIVE_SIGNAL_TP_WAITING).format(index=i),callback_data=cb("n",x.id))] for i in range(1,len(x.take_profits)+1)]);rows.append([InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_SL_WAITING,callback_data=cb("n",x.id))])
 else:rows.append([InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_CONTINUE,callback_data=cb("r",x.id))])
 rows.append([InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_CANCEL,callback_data=cb("c",x.id,page))]);rows.append([InlineKeyboardButton(text=texts.BUTTON_BACK_STEP,callback_data=cb("l",page))]);return InlineKeyboardMarkup(inline_keyboard=rows)
async def _show(message,actor,service,page=1):
 if actor.kind is not ActorKind.ACTIVE_SIGNALIST:return await message.answer(texts.ACTIVE_SIGNAL_STALE)
 result=await service.list_open(actor.signalist.id,PageRequest(page))
 if not result.items:return await message.answer(texts.ACTIVE_SIGNALS_EMPTY,reply_markup=signalist_menu())
 await message.answer(texts.ACTIVE_SIGNALS_TITLE.format(page=result.page,pages=result.pages),reply_markup=list_keyboard(result))

@router.message(F.text==texts.MENU_ACTIVE_SIGNALS)
async def active_list(message:Message,actor:Actor,active_signal_service):await _show(message,actor,active_signal_service)

@router.callback_query(F.data.startswith(PREFIX))
async def active_callback(callback:CallbackQuery,actor:Actor,state:FSMContext,callback_ack:CallbackAcknowledger,active_signal_service,signal_service):
 action,args=_parse(callback.data);await callback_ack.answer_once()
 if not isinstance(callback.message,Message):return
 if actor.kind is not ActorKind.ACTIVE_SIGNALIST:return await callback.message.answer(texts.ACTIVE_SIGNAL_STALE)
 if action=="n":return
 if action=="m":return await callback.message.answer(texts.DASHBOARD_SIGNALIST,reply_markup=signalist_menu())
 if action=="l":return await _show(callback.message,actor,active_signal_service,args[0] if args else 1)
 if not args:return await callback.message.answer(texts.ACTIVE_SIGNAL_STALE)
 signal_id=args[0];item=await active_signal_service.get_open(signal_id,actor.signalist.id)
 if item is None:return await callback.message.answer(texts.ACTIVE_SIGNAL_STALE)
 page=args[1] if len(args)>1 else 1
 if action=="d":return await callback.message.answer(detail_text(item),reply_markup=detail_keyboard(item,page))
 if action=="r":
  if item.status is not SignalLifecycleStatus.PENDING_DETAILS:return await callback.message.answer(texts.ACTIVE_SIGNAL_STALE)
  # Delegate to the accepted durable Stage-Two callback path.
  from chainura.telegram.signal_routes import signal_callback_handler
  from chainura.telegram.presentation.signal_callbacks import SignalCallback
  return await signal_callback_handler(callback,SignalCallback(kind="continue",flow="resume",ref=str(signal_id),page=1),actor,state,callback_ack,None,signal_service)
 if action=="c":
  markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_CANCEL_YES,callback_data=cb("y",signal_id,page)),InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_CANCEL_NO,callback_data=cb("d",signal_id,page))],[InlineKeyboardButton(text=texts.BUTTON_BACK_STEP,callback_data=cb("d",signal_id,page))]])
  return await callback.message.answer(texts.ACTIVE_SIGNAL_CANCEL_CONFIRM.format(symbol=format_symbol(item.symbol)),reply_markup=markup)
 if action=="y":
  result=await active_signal_service.cancel_signal(signal_id,actor.signalist.id)
  if not result.cancelled:return await callback.message.answer(texts.ACTIVE_SIGNAL_NOT_CANCELLABLE)
  data=await state.get_data()
  if data.get("pending_signal_id")==signal_id:await state.clear()
  return await callback.message.answer(texts.ACTIVE_SIGNAL_CANCELLED_QUEUED if result.publication_count else texts.ACTIVE_SIGNAL_CANCELLED,reply_markup=signalist_menu())
