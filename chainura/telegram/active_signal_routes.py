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
from chainura.telegram.states import SignalCreate,SignalComment
from chainura.trade_id import format_trade_id
from chainura.telegram.presentation.formatting import bidi_ascii

router=Router(name="phase7-active-signals");PREFIX="as7:"
def cb(action,*parts):return PREFIX+":".join((action,*(str(x) for x in parts)))
def _parse(data):
 try:
  parts=data.split(":");return parts[1],tuple(int(x) for x in parts[2:])
 except (ValueError,IndexError,AttributeError):return None,()
def _label(x):return f"{format_trade_id(x.id)} • {x.symbol} • {'لانگ' if x.direction is TradeDirection.LONG else 'شورت'} • {texts.ACTIVE_SIGNAL_PENDING if x.status is SignalLifecycleStatus.PENDING_DETAILS else texts.ACTIVE_SIGNAL_ACTIVE}"
def list_keyboard(page):
 rows=[[InlineKeyboardButton(text=_label(x),callback_data=cb("d",x.id,page.page))] for x in page.items];nav=[]
 if page.page>1:nav.append(InlineKeyboardButton(text=texts.BUTTON_PREVIOUS,callback_data=cb("l",page.page-1)))
 if page.page<page.pages:nav.append(InlineKeyboardButton(text=texts.BUTTON_NEXT,callback_data=cb("l",page.page+1)))
 if nav:rows.append(nav)
 rows.append([InlineKeyboardButton(text=texts.BUTTON_BACK_STEP,callback_data=cb("m"))]);return InlineKeyboardMarkup(inline_keyboard=rows)
def detail_text(x):
 rows=["<b>📋 سیگنال فعال</b>","",f"شناسه معامله: {bidi_ascii(format_trade_id(x.id))}",f"نماد: {format_symbol(x.symbol)}",f"جهت: {'لانگ' if x.direction is TradeDirection.LONG else 'شورت'}",f"ورود: {format_decimal(x.entry)}",f"اهرم: {format_leverage(x.leverage)}",f"وضعیت: {texts.ACTIVE_SIGNAL_PENDING if x.status is SignalLifecycleStatus.PENDING_DETAILS else texts.ACTIVE_SIGNAL_ACTIVE}"]
 if x.status is SignalLifecycleStatus.ACTIVE:
  rows.extend([f"تارگت {i}: {format_decimal(v)}" for i,v in enumerate(x.take_profits,1)]);rows.append(f"حد ضرر: {format_decimal(x.stop_loss)}")
  rows.append(f"حجم باقی‌مانده: {format_decimal(x.remaining_fraction*100)}٪")
  rows.append(f"ریسک فری: {'فعال' if x.risk_free_active else 'استفاده‌شده' if x.risk_free_used else 'استفاده‌نشده'}")
  rows.append(f"سیو سود ۵۰٪: {'استفاده‌شده' if x.save_profit_50_used else 'استفاده‌نشده'}")
  if x.realized_weighted_roi:rows.append(f"بازده محقق‌شده: {format_decimal(x.realized_weighted_roi)}٪")
 return "\n".join(rows)
def detail_keyboard(x,page):
 rows=[]
 if x.status is SignalLifecycleStatus.ACTIVE:
  rows.extend([[InlineKeyboardButton(text=(texts.ACTIVE_SIGNAL_TP_REACHED if i<=x.highest_tp_index else texts.ACTIVE_SIGNAL_TP_WAITING).format(index=i),callback_data=cb("n",x.id))] for i in range(1,len(x.take_profits)+1)]);rows.append([InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_SL_WAITING,callback_data=cb("n",x.id))])
 else:rows.append([InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_CONTINUE,callback_data=cb("r",x.id))])
 actions=[]
 if x.status is SignalLifecycleStatus.ACTIVE:actions.append(InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_COMMENT,callback_data=cb("o",x.id,page)))
 actions.append(InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_CANCEL,callback_data=cb("c",x.id,page)));rows.append(actions);rows.append([InlineKeyboardButton(text=texts.BUTTON_BACK_STEP,callback_data=cb("l",page))]);return InlineKeyboardMarkup(inline_keyboard=rows)

def comment_choice_keyboard(signal_id,page):
 return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=texts.SIGNAL_COMMENT_WITH_SNAPSHOT,callback_data=cb("s",signal_id,page))],[InlineKeyboardButton(text=texts.SIGNAL_COMMENT_TEXT_ONLY,callback_data=cb("t",signal_id,page))],[InlineKeyboardButton(text=texts.SIGNAL_COMMENT_ABORT,callback_data=cb("x",signal_id,page))]])

async def clear_cancelled_signal_fsm(state,signal_id):
 data=await state.get_data();current=await state.get_state()
 pending_same=data.get("pending_signal_id")==signal_id
 comment_same=(current in (SignalComment.waiting_text.state,SignalComment.waiting_snapshot_choice.state) and data.get("comment_signal_id")==signal_id)
 if pending_same or comment_same:await state.clear()

@router.message(SignalComment.waiting_text)
async def comment_text(message:Message,state:FSMContext,actor:Actor):
 data=await state.get_data();raw=message.text or ""
 if actor.kind is not ActorKind.ACTIVE_SIGNALIST:await state.clear();return await message.answer(texts.SIGNAL_COMMENT_STALE)
 if not raw.strip():return await message.answer(texts.SIGNAL_COMMENT_EMPTY)
 if len(raw)>4096:return await message.answer(texts.SIGNAL_COMMENT_TOO_LONG)
 await state.update_data(comment_text=raw);await state.set_state(SignalComment.waiting_snapshot_choice)
 return await message.answer(texts.SIGNAL_COMMENT_SNAPSHOT_QUESTION,reply_markup=comment_choice_keyboard(data["comment_signal_id"],data.get("comment_page",1)))
async def _show(message,actor,service,page=1):
 if actor.kind is not ActorKind.ACTIVE_SIGNALIST:return await message.answer(texts.ACTIVE_SIGNAL_STALE)
 result=await service.list_open(actor.signalist.id,PageRequest(page))
 if not result.items:return await message.answer(texts.ACTIVE_SIGNALS_EMPTY,reply_markup=signalist_menu())
 await message.answer(texts.ACTIVE_SIGNALS_TITLE.format(page=result.page,pages=result.pages),reply_markup=list_keyboard(result))

@router.message(F.text==texts.MENU_ACTIVE_SIGNALS)
async def active_list(message:Message,actor:Actor,active_signal_service):await _show(message,actor,active_signal_service)

@router.callback_query(F.data.startswith(PREFIX))
async def active_callback(callback:CallbackQuery,actor:Actor,state:FSMContext,callback_ack:CallbackAcknowledger,active_signal_service,signal_service,signal_comment_service=None):
 action,args=_parse(callback.data);await callback_ack.answer_once()
 if not isinstance(callback.message,Message):return
 from chainura.telegram.signal_routes import reconcile_stage_one_creating
 if await reconcile_stage_one_creating(callback,callback.message,state,signal_service,
                                        actor=actor) is not None:return
 if actor.kind is not ActorKind.ACTIVE_SIGNALIST:return await callback.message.answer(texts.ACTIVE_SIGNAL_STALE)
 if action=="n":return
 if action=="m":return await callback.message.answer(texts.DASHBOARD_SIGNALIST,reply_markup=signalist_menu())
 if action=="l":return await _show(callback.message,actor,active_signal_service,args[0] if args else 1)
 if not args:return await callback.message.answer(texts.ACTIVE_SIGNAL_STALE)
 signal_id=args[0];item=await active_signal_service.get_open(signal_id,actor.signalist.id)
 if item is None:
  if action in ("s","t","x"):await state.clear()
  return await callback.message.answer(texts.ACTIVE_SIGNAL_STALE)
 page=args[1] if len(args)>1 else 1
 if action=="d":return await callback.message.answer(detail_text(item),reply_markup=detail_keyboard(item,page))
 if action=="o":
  if item.status is not SignalLifecycleStatus.ACTIVE or signal_comment_service is None or not await signal_comment_service.eligible(signal_id,actor.signalist.id):return await callback.message.answer(texts.SIGNAL_COMMENT_STALE)
  await state.set_state(SignalComment.waiting_text);await state.set_data({"comment_signal_id":signal_id,"comment_page":page})
  return await callback.message.answer(texts.signal_comment_prompt(format_trade_id(signal_id)))
 if action=="x":
  current=await state.get_state()
  if current in (SignalComment.waiting_text.state,SignalComment.waiting_snapshot_choice.state):await state.clear()
  return await callback.message.answer(texts.SIGNAL_COMMENT_CANCELLED)
 if action in ("s","t"):
  data=await state.get_data()
  if await state.get_state()!=SignalComment.waiting_snapshot_choice.state or data.get("comment_signal_id")!=signal_id:return await callback.message.answer(texts.SIGNAL_COMMENT_STALE)
  result=await signal_comment_service.create(signal_id,actor.signalist.id,data.get("comment_text",""),action=="s")
  if result.reason=="caption_too_long":return await callback.message.answer(texts.SIGNAL_COMMENT_CAPTION_TOO_LONG,reply_markup=comment_choice_keyboard(signal_id,page))
  await state.clear()
  if result.created:return await callback.message.answer(
      texts.signal_comment_queued(format_trade_id(signal_id)))
  return await callback.message.answer({"market_unavailable":texts.SIGNAL_COMMENT_MARKET_UNAVAILABLE,"no_destinations":texts.SIGNAL_COMMENT_NO_DESTINATIONS}.get(result.reason,texts.SIGNAL_COMMENT_STALE))
 if action=="r":
  if item.status is not SignalLifecycleStatus.PENDING_DETAILS:return await callback.message.answer(texts.ACTIVE_SIGNAL_STALE)
  # Delegate to the accepted durable Stage-Two callback path.
  from chainura.telegram.signal_routes import signal_callback_handler
  from chainura.telegram.presentation.signal_callbacks import SignalCallback
  return await signal_callback_handler(callback,SignalCallback(kind="continue",flow="resume",ref=str(signal_id),page=1),actor,state,callback_ack,None,signal_service)
 if action=="c":
  markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_CANCEL_YES,callback_data=cb("y",signal_id,page)),InlineKeyboardButton(text=texts.ACTIVE_SIGNAL_CANCEL_NO,callback_data=cb("d",signal_id,page))],[InlineKeyboardButton(text=texts.BUTTON_BACK_STEP,callback_data=cb("d",signal_id,page))]])
  return await callback.message.answer(texts.active_signal_cancel_confirm(
      format_trade_id(signal_id),format_symbol(item.symbol)),reply_markup=markup)
 if action=="y":
  result=await active_signal_service.cancel_signal(signal_id,actor.signalist.id)
  if not result.cancelled:return await callback.message.answer(texts.ACTIVE_SIGNAL_NOT_CANCELLABLE)
  await clear_cancelled_signal_fsm(state,signal_id)
  return await callback.message.answer(texts.active_signal_cancelled(
      format_trade_id(signal_id),bool(result.publication_count)),reply_markup=signalist_menu())
