from __future__ import annotations
from decimal import Decimal
from uuid import uuid4
import asyncio

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from chainura.application.access import Actor, ActorKind
from chainura.application.signal_service import CreateSignalCommand
from chainura.domain.destination import Exchange
from chainura.domain.signal import (PriceSourceType, SignalMode, TradeDirection,
    parse_leverage, parse_price)
from chainura.errors import (ApplicationError, EntryOutsideToleranceError,
    InconsistentReferencePriceError, InvalidSignalInputError,
    ReferencePriceUnavailableError)
from chainura.telegram.middlewares import CallbackAcknowledger
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import (remove_menu, signal_directions,
    signal_exchanges, signal_form_navigation, signal_native_navigation,
    signal_results, signal_review_actions, signal_shared_navigation,
    signal_tp_actions, signalist_menu)
from chainura.telegram.presentation.signal_callbacks import SignalCallback
from chainura.telegram.states import SignalCreate

router=Router(name="phase5-signal-create")
_START_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}

def _token(): return uuid4().hex[:10]

async def _owned(state:FSMContext, expected, flow:str, operation:str|None=None, context:dict|None=None):
    if await state.get_state() != expected.state: return None
    data=await state.get_data()
    if data.get("flow_token") != flow: return None
    if operation and data.get(operation[0]) != operation[1]: return None
    if context and any(data.get(k) != v for k,v in context.items()): return None
    return data

async def _cancel(message:Message,state:FSMContext):
    await state.clear(); await message.answer(texts.FLOW_CANCELLED,reply_markup=signalist_menu())

@router.message(F.text == texts.MENU_CREATE_SIGNAL)
async def start_signal(message:Message,actor:Actor,state:FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST:
        await message.answer(texts.INACTIVE if actor.kind is ActorKind.INACTIVE_SIGNALIST else texts.ARCHIVED if actor.kind is ActorKind.ARCHIVED_SIGNALIST else texts.UNKNOWN,reply_markup=remove_menu()); return
    key=(message.chat.id,message.from_user.id)
    lock=_START_LOCKS.setdefault(key,asyncio.Lock())
    async with lock:
        flow=_token(); creation=uuid4().hex
        await state.set_state(SignalCreate.shared_search)
        await state.set_data({"flow_token":flow,"creation_token":creation,
                              "signalist_id":actor.signalist.id})
        if not await _owned(state,SignalCreate.shared_search,flow): return
    await message.answer(texts.ASK_SHARED_SYMBOL,
                         reply_markup=signal_shared_navigation(flow))

@router.message(SignalCreate.shared_search, F.text, ~F.text.startswith("/"), F.text != texts.MENU_MAIN, F.text != texts.MENU_CREATE_SIGNAL)
async def shared_query(message:Message,actor:Actor,state:FSMContext,symbol_catalog):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _cancel(message,state); return
    data=await state.get_data(); flow=data.get("flow_token"); token=_token(); query=message.text.strip()
    await state.update_data(search_token=token,shared_query=query)
    try: result=await symbol_catalog.search_shared(query,1)
    except ApplicationError:
        if await _owned(state,SignalCreate.shared_search,flow,("search_token",token),{"shared_query":query}):
            await message.answer(texts.SIGNAL_PROVIDER_FAILURE,
                                 reply_markup=signal_shared_navigation(flow))
        return
    if not await _owned(state,SignalCreate.shared_search,flow,("search_token",token),{"shared_query":query}): return
    items=[_item(x) for x in result.items]; await state.update_data(search_results=items,search_page=result.page,search_pages=result.pages)
    if not items:
        await message.answer(texts.SIGNAL_EMPTY_SEARCH,
                             reply_markup=signal_shared_navigation(flow)); return
    await message.answer(texts.signal_search_title(result.page,result.pages),reply_markup=signal_results(result.items,result.page,result.pages,flow))

@router.message(SignalCreate.native_search, F.text, ~F.text.startswith("/"), F.text != texts.MENU_MAIN, F.text != texts.MENU_CREATE_SIGNAL)
async def native_query(message:Message,actor:Actor,state:FSMContext,symbol_catalog):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _cancel(message,state); return
    data=await state.get_data(); flow=data.get("flow_token"); exchange=data.get("selected_exchange"); token=_token(); query=message.text.strip()
    await state.update_data(search_token=token,native_query=query)
    try: result=await symbol_catalog.search_exchange(Exchange(exchange),query,1)
    except (ApplicationError,ValueError):
        if await _owned(state,SignalCreate.native_search,flow,("search_token",token),{"selected_exchange":exchange,"native_query":query}):
            await message.answer(texts.SIGNAL_PROVIDER_FAILURE,
                                 reply_markup=signal_native_navigation(flow))
        return
    if not await _owned(state,SignalCreate.native_search,flow,("search_token",token),{"selected_exchange":exchange,"native_query":query}): return
    items=[_item(x) for x in result.items]; await state.update_data(search_results=items,search_page=result.page,search_pages=result.pages)
    if not items:
        await message.answer(texts.SIGNAL_EMPTY_SEARCH,
                             reply_markup=signal_native_navigation(flow)); return
    await message.answer(texts.signal_search_title(result.page,result.pages),reply_markup=signal_results(result.items,result.page,result.pages,flow,native=True))

@router.message(SignalCreate.entry, F.text, ~F.text.startswith("/"))
async def entry_input(message:Message,actor:Actor,state:FSMContext,signal_service):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _cancel(message,state); return
    try: entry=parse_price(message.text)
    except InvalidSignalInputError: await message.answer(texts.INVALID_INPUT); return
    data=await state.get_data(); flow=data["flow_token"]; token=_token(); await state.update_data(entry_validation_token=token,entry_price=str(entry))
    command=_command({**data,"entry_price":str(entry),"leverage":1,"take_profits":[str(entry+Decimal("1"))],"stop_loss":str(entry-Decimal("1")) if entry>1 else str(entry/2)})
    try: result=await signal_service.validate_entry(command)
    except EntryOutsideToleranceError:
        if await _owned(state,SignalCreate.entry,flow,("entry_validation_token",token),{"entry_price":str(entry)}): await message.answer(texts.ENTRY_OUT_OF_TOLERANCE)
        return
    except ReferencePriceUnavailableError:
        if await _owned(state,SignalCreate.entry,flow,("entry_validation_token",token),{"entry_price":str(entry)}): await message.answer(texts.REFERENCE_UNAVAILABLE)
        return
    except (InconsistentReferencePriceError, ApplicationError):
        if await _owned(state,SignalCreate.entry,flow,("entry_validation_token",token),{"entry_price":str(entry)}): await message.answer(texts.TEMPORARY_ERROR)
        return
    if not await _owned(state,SignalCreate.entry,flow,("entry_validation_token",token),{"entry_price":str(entry)}): return
    await state.update_data(reference_price=str(result.reference_price),price_source_type=result.price_source_type.value)
    await state.set_state(SignalCreate.leverage); await message.answer(texts.SIGNAL_ENTRY_VALID+"\n\n"+texts.SIGNAL_ASK_LEVERAGE,reply_markup=signal_form_navigation(flow,"entry"))

@router.message(SignalCreate.leverage, F.text, ~F.text.startswith("/"))
async def leverage_input(message:Message,actor:Actor,state:FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _cancel(message,state); return
    try: leverage=parse_leverage(message.text)
    except InvalidSignalInputError: await message.answer(texts.INVALID_INPUT); return
    await state.update_data(leverage=leverage,take_profits=[]); await state.set_state(SignalCreate.take_profits)
    await message.answer(texts.SIGNAL_ASK_TP,reply_markup=signal_tp_actions((await state.get_data())["flow_token"],False))

@router.message(SignalCreate.take_profits, F.text, ~F.text.startswith("/"))
async def tp_input(message:Message,actor:Actor,state:FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _cancel(message,state); return
    try: value=parse_price(message.text)
    except InvalidSignalInputError: await message.answer(texts.INVALID_INPUT); return
    data=await state.get_data(); entry=Decimal(data["entry_price"]); existing=tuple(Decimal(x) for x in data.get("take_profits",[])); direction=data["direction"]
    good=(direction=="long" and value>entry and (not existing or value>existing[-1])) or (direction=="short" and value<entry and (not existing or value<existing[-1]))
    if not good or value in existing: await message.answer(texts.SIGNAL_INVALID_DIRECTIONAL_PRICE); return
    values=existing+(value,); await state.update_data(take_profits=[str(x) for x in values])
    await message.answer(texts.signal_tp_list(values),reply_markup=signal_tp_actions(data["flow_token"],True))

@router.message(SignalCreate.stop_loss, F.text, ~F.text.startswith("/"))
async def sl_input(message:Message,actor:Actor,state:FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _cancel(message,state); return
    try: value=parse_price(message.text)
    except InvalidSignalInputError: await message.answer(texts.INVALID_INPUT); return
    data=await state.get_data(); entry=Decimal(data["entry_price"])
    if not ((data["direction"]=="long" and value<entry) or (data["direction"]=="short" and value>entry)):
        await message.answer(texts.SIGNAL_INVALID_DIRECTIONAL_PRICE); return
    await state.update_data(stop_loss=str(value)); await state.set_state(SignalCreate.review); data=await state.get_data()
    await message.answer(texts.signal_review(data),reply_markup=signal_review_actions(data["flow_token"]))

@router.callback_query(SignalCallback.filter())
async def signal_callback_handler(callback:CallbackQuery,callback_data:SignalCallback,actor:Actor,state:FSMContext,callback_ack:CallbackAcknowledger,symbol_catalog,signal_service):
    await callback_ack.answer_once()
    if callback.message is None or not hasattr(callback.message, "answer"): return
    data=await state.get_data(); flow=callback_data.flow
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST:
        await state.clear(); await callback.message.answer(texts.INACTIVE if actor.kind is ActorKind.INACTIVE_SIGNALIST else texts.ARCHIVED,reply_markup=remove_menu()); return
    if data.get("flow_token") != flow: return
    kind=callback_data.kind
    if kind=="cancel": await _cancel(callback.message,state); return
    if kind=="out" and await state.get_state()==SignalCreate.shared_search.state:
        await state.set_state(SignalCreate.exchange_selection); await callback.message.answer(texts.SIGNAL_SELECT_EXCHANGE,reply_markup=signal_exchanges(flow)); return
    if kind=="ex" and await state.get_state()==SignalCreate.exchange_selection.state:
        try: exchange=Exchange(callback_data.ref)
        except ValueError: return
        await state.update_data(mode="single_exchange",selected_exchange=exchange.value,search_results=[]); await state.set_state(SignalCreate.native_search); await callback.message.answer(texts.SIGNAL_ASK_NATIVE,reply_markup=signal_native_navigation(flow)); return
    if kind in {"sp","np"}:
        expected=SignalCreate.shared_search if kind=="sp" else SignalCreate.native_search
        if await state.get_state()!=expected.state or callback_data.page < 1: return
        query=data.get("shared_query" if kind=="sp" else "native_query"); token=_token(); await state.update_data(search_token=token)
        context = ({"shared_query":query} if kind=="sp" else
                   {"selected_exchange":data["selected_exchange"], "native_query":query})
        try:
            result=await symbol_catalog.search_shared(query,callback_data.page) if kind=="sp" else await symbol_catalog.search_exchange(Exchange(data["selected_exchange"]),query,callback_data.page)
        except ApplicationError:
            if not await _owned(state,expected,flow,("search_token",token),context):
                return
            markup = signal_shared_navigation(flow) if kind == "sp" else signal_native_navigation(flow)
            await callback.message.answer(texts.SIGNAL_PROVIDER_FAILURE, reply_markup=markup)
            return
        if not await _owned(state,expected,flow,("search_token",token),context): return
        await state.update_data(search_results=[_item(x) for x in result.items],search_page=result.page,search_pages=result.pages)
        await callback.message.answer(texts.signal_search_title(result.page,result.pages),reply_markup=signal_results(result.items,result.page,result.pages,flow,native=kind=="np")); return
    if kind in {"ss","ns"}:
        expected=SignalCreate.shared_search if kind=="ss" else SignalCreate.native_search
        if await state.get_state()!=expected.state: return
        if callback_data.page != data.get("search_page"): return
        try: selected=data["search_results"][int(callback_data.ref)]
        except (KeyError,ValueError,IndexError): return
        token=_token()
        if kind == "ss":
            context = {"shared_query":data.get("shared_query"),
                       "verification_symbol":selected["canonical_symbol"]}
            await state.update_data(search_token=token,
                                    verification_symbol=selected["canonical_symbol"])
        else:
            context = {"selected_exchange":data["selected_exchange"],
                       "native_query":data.get("native_query"),
                       "verification_native":selected["native_symbol"]}
            await state.update_data(search_token=token,
                                    verification_native=selected["native_symbol"])
        try:
            verified=await symbol_catalog.get_shared(selected["canonical_symbol"]) if kind=="ss" else await symbol_catalog.get_exchange_contract(Exchange(data["selected_exchange"]),selected["native_symbol"])
        except ApplicationError:
            if not await _owned(state,expected,flow,("search_token",token),context):
                return
            markup = signal_shared_navigation(flow) if kind == "ss" else signal_native_navigation(flow)
            await callback.message.answer(texts.SIGNAL_PROVIDER_FAILURE, reply_markup=markup)
            return
        if verified is None or not await _owned(state,expected,flow,("search_token",token),context): return
        selected=_item(verified)
        if kind=="ss": selected.update(mode="composite",selected_exchange=None,native_symbol=None)
        else: selected.update(mode="single_exchange",selected_exchange=data["selected_exchange"])
        await state.update_data(**selected); await state.set_state(SignalCreate.direction); await callback.message.answer(texts.SIGNAL_SELECT_DIRECTION,reply_markup=signal_directions(flow)); return
    if kind=="dir" and await state.get_state()==SignalCreate.direction.state and callback_data.ref in {"long","short"}:
        await state.update_data(direction=callback_data.ref); await state.set_state(SignalCreate.entry); await callback.message.answer(texts.SIGNAL_ASK_ENTRY,reply_markup=signal_form_navigation(flow,"direction")); return
    if kind=="tp" and await state.get_state()==SignalCreate.take_profits.state:
        if callback_data.ref=="finish":
            if not data.get("take_profits"): await callback.message.answer(texts.SIGNAL_FINISH_TP_BLOCKED); return
            await state.set_state(SignalCreate.stop_loss); await callback.message.answer(texts.SIGNAL_ASK_SL,reply_markup=signal_form_navigation(flow,"tp"))
        elif callback_data.ref=="add": await callback.message.answer(texts.SIGNAL_ASK_TP)
        return
    if kind=="confirm" and await state.get_state()==SignalCreate.review.state:
        token=_token(); await state.update_data(final_validation_token=token); command=_command(await state.get_data())
        try: result=await signal_service.validate_entry(command)
        except EntryOutsideToleranceError:
            if await _owned(state,SignalCreate.review,flow,("final_validation_token",token)): await callback.message.answer(texts.FINAL_ENTRY_OUT_OF_TOLERANCE)
            return
        except ReferencePriceUnavailableError:
            if await _owned(state,SignalCreate.review,flow,("final_validation_token",token)): await callback.message.answer(texts.REFERENCE_UNAVAILABLE)
            return
        except (InconsistentReferencePriceError, ApplicationError):
            if await _owned(state,SignalCreate.review,flow,("final_validation_token",token)): await callback.message.answer(texts.TEMPORARY_ERROR)
            return
        if not await _owned(state,SignalCreate.review,flow,("final_validation_token",token),{"canonical_symbol":command.canonical_symbol}): return
        try: await signal_service.create_validated(command,result)
        except ApplicationError: await callback.message.answer(texts.TEMPORARY_ERROR); return
        if not await _owned(state,SignalCreate.review,flow,("final_validation_token",token)): return
        await state.clear(); await callback.message.answer(texts.SIGNAL_RECORDED_SUCCESS,reply_markup=signalist_menu()); return
    if kind=="back":
        allowed={
            "shared":SignalCreate.exchange_selection.state,
            "exchange":SignalCreate.native_search.state,
            "native-shared":SignalCreate.native_search.state,
            "symbol":SignalCreate.direction.state,
            "direction":SignalCreate.entry.state,
            "entry":SignalCreate.leverage.state,
            "leverage":SignalCreate.take_profits.state,
            "tp":SignalCreate.stop_loss.state,
            "sl":SignalCreate.review.state,
        }
        if allowed.get(callback_data.ref) == await state.get_state():
            await _back(callback.message,state,data,callback_data.ref,flow)

async def _back(message,state,data,target,flow):
    if target=="shared": await state.set_state(SignalCreate.shared_search); await state.update_data(selected_exchange=None); await message.answer(texts.ASK_SHARED_SYMBOL,reply_markup=signal_shared_navigation(flow))
    elif target=="exchange": await state.set_state(SignalCreate.exchange_selection); await message.answer(texts.SIGNAL_SELECT_EXCHANGE,reply_markup=signal_exchanges(flow))
    elif target=="native-shared": await state.set_state(SignalCreate.shared_search); await state.update_data(selected_exchange=None,search_results=[]); await message.answer(texts.ASK_SHARED_SYMBOL,reply_markup=signal_shared_navigation(flow))
    elif target=="symbol":
        if data.get("mode")=="single_exchange": await state.set_state(SignalCreate.native_search); await message.answer(texts.SIGNAL_ASK_NATIVE,reply_markup=signal_native_navigation(flow))
        else: await state.set_state(SignalCreate.shared_search); await message.answer(texts.ASK_SHARED_SYMBOL,reply_markup=signal_shared_navigation(flow))
    elif target=="direction": await state.set_state(SignalCreate.direction); await message.answer(texts.SIGNAL_SELECT_DIRECTION,reply_markup=signal_directions(flow))
    elif target=="entry": await state.set_state(SignalCreate.entry); await message.answer(texts.SIGNAL_ASK_ENTRY,reply_markup=signal_form_navigation(flow,"direction"))
    elif target=="leverage": await state.set_state(SignalCreate.leverage); await message.answer(texts.SIGNAL_ASK_LEVERAGE,reply_markup=signal_form_navigation(flow,"entry"))
    elif target=="tp": await state.set_state(SignalCreate.take_profits); await message.answer(texts.SIGNAL_ASK_TP,reply_markup=signal_tp_actions(flow,bool(data.get("take_profits"))))
    elif target=="sl": await state.set_state(SignalCreate.stop_loss); await message.answer(texts.SIGNAL_ASK_SL,reply_markup=signal_form_navigation(flow,"tp"))

def _item(value): return {"canonical_symbol":value.canonical_symbol,"native_symbol":value.native_symbol,"base_asset":value.base_asset,"quote_asset":value.quote_asset}

def _command(data):
    return CreateSignalCommand(signalist_id=data["signalist_id"] if "signalist_id" in data else data.get("actor_signalist_id",1),creation_token=data["creation_token"],mode=SignalMode(data["mode"]),canonical_symbol=data["canonical_symbol"],base_asset=data["base_asset"],quote_asset=data["quote_asset"],selected_exchange=Exchange(data["selected_exchange"]) if data.get("selected_exchange") else None,native_symbol=data.get("native_symbol"),direction=TradeDirection(data["direction"]),entry_price=Decimal(data["entry_price"]),leverage=int(data["leverage"]),take_profits=tuple(Decimal(x) for x in data["take_profits"]),stop_loss=Decimal(data["stop_loss"]))
