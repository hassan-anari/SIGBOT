from __future__ import annotations
from decimal import Decimal
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from chainura.application.access import Actor, ActorKind
from chainura.application.signal_service import CreateSignalCommand, EntryValidationRequest
from chainura.domain.destination import Exchange
from chainura.domain.signal import (PriceSourceType, SignalMode, TradeDirection,
    parse_leverage, parse_price)
from chainura.errors import (ApplicationError, EntryOutsideToleranceError,
    InconsistentReferencePriceError, InvalidSignalInputError,
    ReferencePriceUnavailableError, SignalistCannotCreateSignalError)
from chainura.telegram.middlewares import CallbackAcknowledger
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import (remove_menu, signal_directions,
    signal_exchanges, signal_form_navigation, signal_native_navigation,
    signal_results, signal_review_actions, signal_shared_navigation,
    signal_tp_actions, signalist_menu)
from chainura.telegram.presentation.signal_callbacks import SignalCallback
from chainura.telegram.states import SignalCreate
from chainura.telegram.phase5_coordination import (FinalizationClaim,
    identity_from_callback, identity_from_message, phase5_coordination)

router=Router(name="phase5-signal-create")
_TOP_LEVEL={texts.MENU_MAIN,texts.MENU_CREATE_SIGNAL,texts.MENU_SIGNALIST_STATUS,
            texts.MENU_ADMIN_STATUS,texts.MENU_HELP,texts.MENU_ADMIN_SIGNALISTS,
            texts.MENU_ADMIN_DESTINATIONS}

def _token(): return uuid4().hex[:10]

def phase5_lock(event):
    identity = (identity_from_callback(event) if hasattr(event, "message")
                else identity_from_message(event))
    return phase5_coordination.hold(identity)

def _access_text(actor):
    return (texts.INACTIVE if actor.kind is ActorKind.INACTIVE_SIGNALIST else
            texts.ARCHIVED if actor.kind is ActorKind.ARCHIVED_SIGNALIST else texts.UNKNOWN)

async def reconcile_finalizing(event,message,state,signal_service,*,show_live=True,actor=None):
    identity=(identity_from_callback(event) if hasattr(event,"message")
              else identity_from_message(event))
    recovery_claim=None
    async with phase5_coordination.hold(identity):
        if await state.get_state()!=SignalCreate.finalizing.state: return None
        data=await state.get_data()
        expected=(data.get("flow_token"),data.get("creation_token"),
                  data.get("final_validation_token"),data.get("canonical_symbol"))
        live=phase5_coordination.live_claim(identity)
        if live and (live.flow_token,live.creation_token,live.final_validation_token,
                     live.canonical_symbol)==expected:
            outcome="live"
        else:
            outcome=None
            signalist_id=data.get("signalist_id"); creation_token=data.get("creation_token")
    if outcome=="live":
        if show_live: await message.answer(texts.SIGNAL_FINALIZING)
        return outcome
    if signal_service is None:
        await message.answer(texts.TEMPORARY_ERROR); return "error"
    try: signal=await signal_service.get_by_creation_token(creation_token,signalist_id)
    except ApplicationError:
        await message.answer(texts.TEMPORARY_ERROR); return "error"
    async with phase5_coordination.hold(identity):
        if await state.get_state()!=SignalCreate.finalizing.state: return "stale"
        current=await state.get_data()
        if ((current.get("flow_token"),current.get("creation_token"),
             current.get("final_validation_token"),current.get("canonical_symbol"))!=expected
                or phase5_coordination.live_claim(identity) is not None): return "stale"
        if signal is not None:
            recovery_claim=FinalizationClaim(identity,*expected)
            phase5_coordination.register_claim(recovery_claim)
            outcome="success"
        elif actor is not None and actor.kind is not ActorKind.ACTIVE_SIGNALIST:
            await state.clear(); outcome="denied"
        else:
            preserved={k:v for k,v in current.items()
                       if k not in {"final_validation_token"}}
            await state.set_data(preserved); await state.set_state(SignalCreate.review)
            outcome="review"
    if outcome=="success":
        try:
            await message.answer(texts.SIGNAL_RECORDED_SUCCESS,reply_markup=signalist_menu())
            async with phase5_coordination.hold(identity):
                if await _owned(state,SignalCreate.finalizing,expected[0],
                        ("final_validation_token",expected[2]),
                        {"creation_token":expected[1],"canonical_symbol":expected[3]}):
                    await state.clear()
        finally:
            phase5_coordination.remove_claim(recovery_claim)
    elif outcome=="denied":
        await message.answer(_access_text(actor),reply_markup=remove_menu())
    else: await message.answer(texts.signal_review(preserved),reply_markup=signal_review_actions(preserved["flow_token"]))
    return outcome

async def _reject_actor(message,state,actor,event=None):
    async with phase5_lock(event or message):
        if await state.get_state()!=SignalCreate.finalizing.state:
            await state.clear(); cleared=True
        else: cleared=False
    if cleared: await message.answer(_access_text(actor),reply_markup=remove_menu())
    return cleared

async def _owned(state:FSMContext, expected, flow:str, operation:str|None=None, context:dict|None=None):
    if await state.get_state() != expected.state: return None
    data=await state.get_data()
    if data.get("flow_token") != flow: return None
    if operation and data.get(operation[0]) != operation[1]: return None
    if context and any(data.get(k) != v for k,v in context.items()): return None
    return data

async def _cancel(message:Message,state:FSMContext,flow:str|None=None,event=None,signal_service=None):
    reconciliation=await reconcile_finalizing(event or message,message,state,
        signal_service)
    if reconciliation is not None: return
    async with phase5_lock(event or message):
        if flow is not None and (await state.get_data()).get("flow_token") != flow:
            return
        if await state.get_state() == SignalCreate.finalizing.state:
            finalizing=True
        else:
            finalizing=False
            await state.clear()
    if finalizing:
        await message.answer(texts.SIGNAL_FINALIZING)
    else:
        await message.answer(texts.FLOW_CANCELLED,reply_markup=signalist_menu())

@router.message(F.text == texts.MENU_CREATE_SIGNAL)
async def start_signal(message:Message,actor:Actor,state:FSMContext,signal_service=None):
    if await reconcile_finalizing(message,message,state,signal_service,actor=actor) is not None: return
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST:
        await _reject_actor(message,state,actor); return
    async with phase5_lock(message):
        if await state.get_state() == SignalCreate.finalizing.state:
            finalizing=True
        else:
            finalizing=False
            flow=_token(); creation=uuid4().hex
            await state.set_state(SignalCreate.shared_search)
            await state.set_data({"flow_token":flow,"creation_token":creation,
                                  "signalist_id":actor.signalist.id})
            if not await _owned(state,SignalCreate.shared_search,flow): return
    if finalizing:
        await message.answer(texts.SIGNAL_FINALIZING)
        return
    await message.answer(texts.ASK_SHARED_SYMBOL,
                         reply_markup=signal_shared_navigation(flow))

@router.message(SignalCreate.finalizing)
async def finalizing_input(message:Message,actor:Actor,state:FSMContext,signal_service):
    await reconcile_finalizing(message,message,state,signal_service,actor=actor)

@router.message(SignalCreate.shared_search, F.text, ~F.text.startswith("/"), ~F.text.in_(_TOP_LEVEL))
async def shared_query(message:Message,actor:Actor,state:FSMContext,symbol_catalog):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _reject_actor(message,state,actor); return
    query=message.text.strip(); token=_token()
    async with phase5_lock(message):
        data=await state.get_data(); flow=data.get("flow_token")
        if not await _owned(state,SignalCreate.shared_search,flow): return
        await state.update_data(search_token=token,shared_query=query)
    try: result=await symbol_catalog.search_shared(query,1)
    except ApplicationError:
        async with phase5_lock(message):
            owned=bool(await _owned(state,SignalCreate.shared_search,flow,("search_token",token),{"shared_query":query}))
        if owned:
            await message.answer(texts.SIGNAL_PROVIDER_FAILURE,
                                 reply_markup=signal_shared_navigation(flow))
        return
    items=[_item(x) for x in result.items]
    async with phase5_lock(message):
        if not await _owned(state,SignalCreate.shared_search,flow,("search_token",token),{"shared_query":query}): return
        await state.update_data(search_results=items,search_page=result.page,search_pages=result.pages)
    if not items:
        await message.answer(texts.SIGNAL_EMPTY_SEARCH,
                             reply_markup=signal_shared_navigation(flow)); return
    await message.answer(texts.signal_search_title(result.page,result.pages),reply_markup=signal_results(result.items,result.page,result.pages,flow))

@router.message(SignalCreate.native_search, F.text, ~F.text.startswith("/"), ~F.text.in_(_TOP_LEVEL))
async def native_query(message:Message,actor:Actor,state:FSMContext,symbol_catalog):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _reject_actor(message,state,actor); return
    token=_token(); query=message.text.strip()
    async with phase5_lock(message):
        data=await state.get_data(); flow=data.get("flow_token"); exchange=data.get("selected_exchange")
        if not await _owned(state,SignalCreate.native_search,flow,context={"selected_exchange":exchange}): return
        await state.update_data(search_token=token,native_query=query)
    try: result=await symbol_catalog.search_exchange(Exchange(exchange),query,1)
    except (ApplicationError,ValueError):
        async with phase5_lock(message):
            owned=bool(await _owned(state,SignalCreate.native_search,flow,("search_token",token),{"selected_exchange":exchange,"native_query":query}))
        if owned:
            await message.answer(texts.SIGNAL_PROVIDER_FAILURE,
                                 reply_markup=signal_native_navigation(flow))
        return
    items=[_item(x) for x in result.items]
    async with phase5_lock(message):
        if not await _owned(state,SignalCreate.native_search,flow,("search_token",token),{"selected_exchange":exchange,"native_query":query}): return
        await state.update_data(search_results=items,search_page=result.page,search_pages=result.pages)
    if not items:
        await message.answer(texts.SIGNAL_EMPTY_SEARCH,
                             reply_markup=signal_native_navigation(flow)); return
    await message.answer(texts.signal_search_title(result.page,result.pages),reply_markup=signal_results(result.items,result.page,result.pages,flow,native=True))

@router.message(SignalCreate.entry, F.text, ~F.text.startswith("/"), ~F.text.in_(_TOP_LEVEL))
async def entry_input(message:Message,actor:Actor,state:FSMContext,signal_service):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _reject_actor(message,state,actor); return
    try: entry=parse_price(message.text)
    except InvalidSignalInputError: await message.answer(texts.INVALID_INPUT); return
    token=_token()
    async with phase5_lock(message):
        data=await state.get_data(); flow=data["flow_token"]
        if not await _owned(state,SignalCreate.entry,flow): return
        await state.update_data(entry_validation_token=token,entry_price=str(entry))
    request=EntryValidationRequest(SignalMode(data["mode"]),data["canonical_symbol"],
        Exchange(data["selected_exchange"]) if data.get("selected_exchange") else None,
        data.get("native_symbol"),entry)
    try: result=await signal_service.validate_entry(request)
    except EntryOutsideToleranceError:
        async with phase5_lock(message): owned=bool(await _owned(state,SignalCreate.entry,flow,("entry_validation_token",token),{"entry_price":str(entry)}))
        if owned: await message.answer(texts.ENTRY_OUT_OF_TOLERANCE)
        return
    except ReferencePriceUnavailableError:
        async with phase5_lock(message): owned=bool(await _owned(state,SignalCreate.entry,flow,("entry_validation_token",token),{"entry_price":str(entry)}))
        if owned: await message.answer(texts.REFERENCE_UNAVAILABLE)
        return
    except (InconsistentReferencePriceError, ApplicationError):
        async with phase5_lock(message): owned=bool(await _owned(state,SignalCreate.entry,flow,("entry_validation_token",token),{"entry_price":str(entry)}))
        if owned: await message.answer(texts.TEMPORARY_ERROR)
        return
    async with phase5_lock(message):
        if not await _owned(state,SignalCreate.entry,flow,("entry_validation_token",token),{"entry_price":str(entry)}): return
        await state.update_data(reference_price=str(result.reference_price),price_source_type=result.price_source_type.value)
        await state.set_state(SignalCreate.leverage)
    await message.answer(texts.SIGNAL_ENTRY_VALID+"\n\n"+texts.SIGNAL_ASK_LEVERAGE,reply_markup=signal_form_navigation(flow,"entry"))

@router.message(SignalCreate.leverage, F.text, ~F.text.startswith("/"), ~F.text.in_(_TOP_LEVEL))
async def leverage_input(message:Message,actor:Actor,state:FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _reject_actor(message,state,actor); return
    try: leverage=parse_leverage(message.text)
    except InvalidSignalInputError: await message.answer(texts.INVALID_INPUT); return
    async with phase5_lock(message):
        data=await state.get_data(); flow=data.get("flow_token")
        if not await _owned(state,SignalCreate.leverage,flow): return
        await state.update_data(leverage=leverage,take_profits=[]); await state.set_state(SignalCreate.take_profits)
    await message.answer(texts.SIGNAL_ASK_TP,reply_markup=signal_tp_actions(flow,False))

@router.message(SignalCreate.take_profits, F.text, ~F.text.startswith("/"), ~F.text.in_(_TOP_LEVEL))
async def tp_input(message:Message,actor:Actor,state:FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _reject_actor(message,state,actor); return
    try: value=parse_price(message.text)
    except InvalidSignalInputError: await message.answer(texts.INVALID_INPUT); return
    async with phase5_lock(message):
        data=await state.get_data(); flow=data.get("flow_token")
        if not await _owned(state,SignalCreate.take_profits,flow): return
        entry=Decimal(data["entry_price"]); existing=tuple(Decimal(x) for x in data.get("take_profits",[])); direction=data["direction"]
        good=(direction=="long" and value>entry and (not existing or value>existing[-1])) or (direction=="short" and value<entry and (not existing or value<existing[-1]))
        if not good or value in existing: invalid=True
        else:
            invalid=False; values=existing+(value,); await state.update_data(take_profits=[str(x) for x in values])
    if invalid: await message.answer(texts.SIGNAL_INVALID_DIRECTIONAL_PRICE); return
    await message.answer(texts.signal_tp_list(values),reply_markup=signal_tp_actions(data["flow_token"],True))

@router.message(SignalCreate.stop_loss, F.text, ~F.text.startswith("/"), ~F.text.in_(_TOP_LEVEL))
async def sl_input(message:Message,actor:Actor,state:FSMContext):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST: await _reject_actor(message,state,actor); return
    try: value=parse_price(message.text)
    except InvalidSignalInputError: await message.answer(texts.INVALID_INPUT); return
    async with phase5_lock(message):
        data=await state.get_data(); flow=data.get("flow_token")
        if not await _owned(state,SignalCreate.stop_loss,flow): return
        entry=Decimal(data["entry_price"])
        if not ((data["direction"]=="long" and value<entry) or (data["direction"]=="short" and value>entry)):
            invalid=True
        else:
            invalid=False; await state.update_data(stop_loss=str(value)); await state.set_state(SignalCreate.review); data=await state.get_data()
    if invalid: await message.answer(texts.SIGNAL_INVALID_DIRECTIONAL_PRICE); return
    await message.answer(texts.signal_review(data),reply_markup=signal_review_actions(data["flow_token"]))

@router.callback_query(SignalCallback.filter())
async def signal_callback_handler(callback:CallbackQuery,callback_data:SignalCallback,actor:Actor,state:FSMContext,callback_ack:CallbackAcknowledger,symbol_catalog,signal_service):
    await callback_ack.answer_once()
    if (callback.message is None or callback.from_user is None
            or not hasattr(callback.message, "answer")): return
    kind=callback_data.kind
    reconciliation=await reconcile_finalizing(callback,callback.message,state,
        signal_service,show_live=(kind!="confirm" or actor.kind is not ActorKind.ACTIVE_SIGNALIST),actor=actor)
    if reconciliation is not None:
        if not (reconciliation=="review" and actor.kind is not ActorKind.ACTIVE_SIGNALIST): return
    data=await state.get_data(); flow=callback_data.flow
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST:
        async with phase5_lock(callback):
            current=await state.get_data()
            if current.get("flow_token")!=flow: return
            await state.clear()
        await callback.message.answer(texts.INACTIVE if actor.kind is ActorKind.INACTIVE_SIGNALIST else texts.ARCHIVED,reply_markup=remove_menu()); return
    if data.get("flow_token") != flow: return
    if kind=="cancel": await _cancel(callback.message,state,flow,event=callback,signal_service=signal_service); return
    if kind=="out":
        async with phase5_lock(callback):
            if not await _owned(state,SignalCreate.shared_search,flow): return
            await state.set_state(SignalCreate.exchange_selection)
        await callback.message.answer(texts.SIGNAL_SELECT_EXCHANGE,reply_markup=signal_exchanges(flow)); return
    if kind=="ex":
        try: exchange=Exchange(callback_data.ref)
        except ValueError: return
        async with phase5_lock(callback):
            if not await _owned(state,SignalCreate.exchange_selection,flow): return
            await state.update_data(mode="single_exchange",selected_exchange=exchange.value,search_results=[])
            await state.set_state(SignalCreate.native_search)
        await callback.message.answer(texts.SIGNAL_ASK_NATIVE,reply_markup=signal_native_navigation(flow)); return
    if kind in {"sp","np"}:
        expected=SignalCreate.shared_search if kind=="sp" else SignalCreate.native_search
        if await state.get_state()!=expected.state or callback_data.page < 1: return
        query=data.get("shared_query" if kind=="sp" else "native_query"); token=_token()
        context = ({"shared_query":query} if kind=="sp" else
                   {"selected_exchange":data["selected_exchange"], "native_query":query})
        async with phase5_lock(callback):
            if not await _owned(state,expected,flow,context=context): return
            await state.update_data(search_token=token)
        try:
            result=await symbol_catalog.search_shared(query,callback_data.page) if kind=="sp" else await symbol_catalog.search_exchange(Exchange(data["selected_exchange"]),query,callback_data.page)
        except ApplicationError:
            async with phase5_lock(callback):
                owned=bool(await _owned(state,expected,flow,("search_token",token),context))
            if not owned: return
            markup = signal_shared_navigation(flow) if kind == "sp" else signal_native_navigation(flow)
            await callback.message.answer(texts.SIGNAL_PROVIDER_FAILURE, reply_markup=markup)
            return
        async with phase5_lock(callback):
            if not await _owned(state,expected,flow,("search_token",token),context): return
            await state.update_data(search_results=[_item(x) for x in result.items],search_page=result.page,search_pages=result.pages)
        await callback.message.answer(texts.signal_search_title(result.page,result.pages),reply_markup=signal_results(result.items,result.page,result.pages,flow,native=kind=="np")); return
    if kind in {"ss","ns"}:
        expected=SignalCreate.shared_search if kind=="ss" else SignalCreate.native_search
        token=_token()
        async with phase5_lock(callback):
            current=await state.get_data()
            if not await _owned(state,expected,flow): return
            if callback_data.page != current.get("search_page"): return
            try: selected=current["search_results"][int(callback_data.ref)]
            except (KeyError,ValueError,IndexError): return
            if selected.get("current_price") is None:
                await callback.message.answer(texts.SIGNAL_PRICE_UNAVAILABLE)
                return
            if kind == "ss":
                context={"search_page":callback_data.page,
                         "shared_query":current.get("shared_query"),
                         "verification_symbol":selected["canonical_symbol"]}
                mutation={"search_token":token,"verification_symbol":selected["canonical_symbol"]}
                selected_exchange=None
            else:
                selected_exchange=current["selected_exchange"]
                context={"search_page":callback_data.page,
                         "selected_exchange":selected_exchange,
                         "native_query":current.get("native_query"),
                         "verification_native":selected["native_symbol"]}
                mutation={"search_token":token,"verification_native":selected["native_symbol"]}
            await state.update_data(**mutation)
        try:
            verified=await symbol_catalog.get_shared(selected["canonical_symbol"]) if kind=="ss" else await symbol_catalog.get_exchange_contract(Exchange(selected_exchange),selected["native_symbol"])
        except ApplicationError:
            async with phase5_lock(callback):
                owned=bool(await _owned(state,expected,flow,("search_token",token),context))
            if not owned: return
            markup = signal_shared_navigation(flow) if kind == "ss" else signal_native_navigation(flow)
            await callback.message.answer(texts.SIGNAL_PROVIDER_FAILURE, reply_markup=markup)
            return
        if verified is None: return
        selected=_item(verified)
        if kind=="ss": selected.update(mode="composite",selected_exchange=None,native_symbol=None)
        else: selected.update(mode="single_exchange",selected_exchange=selected_exchange)
        async with phase5_lock(callback):
            if not await _owned(state,expected,flow,("search_token",token),context): return
            await state.update_data(**selected); await state.set_state(SignalCreate.direction)
        await callback.message.answer(texts.SIGNAL_SELECT_DIRECTION,reply_markup=signal_directions(flow)); return
    if kind=="dir" and callback_data.ref in {"long","short"}:
        async with phase5_lock(callback):
            if not await _owned(state,SignalCreate.direction,flow): return
            await state.update_data(direction=callback_data.ref); await state.set_state(SignalCreate.entry)
        await callback.message.answer(texts.SIGNAL_ASK_ENTRY,reply_markup=signal_form_navigation(flow,"direction")); return
    if kind=="tp":
        async with phase5_lock(callback):
            current=await state.get_data()
            if not await _owned(state,SignalCreate.take_profits,flow): return
            if callback_data.ref=="finish" and current.get("take_profits"):
                await state.set_state(SignalCreate.stop_loss); action="finish"
            elif callback_data.ref=="finish": action="blocked"
            elif callback_data.ref=="add": action="add"
            else: return
        if action=="finish": await callback.message.answer(texts.SIGNAL_ASK_SL,reply_markup=signal_form_navigation(flow,"tp"))
        elif action=="blocked": await callback.message.answer(texts.SIGNAL_FINISH_TP_BLOCKED)
        else: await callback.message.answer(texts.SIGNAL_ASK_TP)
        return
    if kind=="confirm":
        token=_token()
        async with phase5_lock(callback):
            if not await _owned(state,SignalCreate.review,flow): return
            current=await state.get_data()
            creation_token=current.get("creation_token")
            canonical_symbol=current.get("canonical_symbol")
            await state.update_data(final_validation_token=token)
            command=_command(await state.get_data())
        try: result=await signal_service.validate_entry(command)
        except EntryOutsideToleranceError:
            async with phase5_lock(callback): owned=bool(await _owned(state,SignalCreate.review,flow,("final_validation_token",token),{"creation_token":creation_token,"canonical_symbol":canonical_symbol}))
            if owned: await callback.message.answer(texts.FINAL_ENTRY_OUT_OF_TOLERANCE)
            return
        except ReferencePriceUnavailableError:
            async with phase5_lock(callback): owned=bool(await _owned(state,SignalCreate.review,flow,("final_validation_token",token),{"creation_token":creation_token,"canonical_symbol":canonical_symbol}))
            if owned: await callback.message.answer(texts.REFERENCE_UNAVAILABLE)
            return
        except (InconsistentReferencePriceError, ApplicationError):
            async with phase5_lock(callback): owned=bool(await _owned(state,SignalCreate.review,flow,("final_validation_token",token),{"creation_token":creation_token,"canonical_symbol":canonical_symbol}))
            if owned: await callback.message.answer(texts.TEMPORARY_ERROR)
            return
        ownership={"creation_token":creation_token,"canonical_symbol":canonical_symbol}
        identity=identity_from_callback(callback)
        claim=None
        try:
            async with phase5_lock(callback):
                if not await _owned(state,SignalCreate.review,flow,("final_validation_token",token),ownership): return
                await state.set_state(SignalCreate.finalizing)
                claim=FinalizationClaim(identity,flow,creation_token,token,canonical_symbol)
                phase5_coordination.register_claim(claim)
            try:
                await signal_service.create_validated(command,result)
            except SignalistCannotCreateSignalError:
                async with phase5_lock(callback):
                    still_owned=bool(await _owned(state,SignalCreate.finalizing,flow,("final_validation_token",token),{"creation_token":creation_token}))
                    if still_owned: await state.clear()
                if still_owned: await callback.message.answer(texts.SIGNALIST_NO_LONGER_ACTIVE,reply_markup=remove_menu())
                return
            except ApplicationError:
                async with phase5_lock(callback):
                    still_owned=bool(await _owned(state,SignalCreate.finalizing,flow,("final_validation_token",token),{"creation_token":creation_token}))
                    if still_owned: await state.set_state(SignalCreate.review)
                if still_owned: await callback.message.answer(texts.TEMPORARY_ERROR)
                return
            async with phase5_lock(callback):
                still_owned=bool(await _owned(state,SignalCreate.finalizing,flow,("final_validation_token",token),{"creation_token":creation_token,"canonical_symbol":canonical_symbol}))
            if still_owned:
                await callback.message.answer(texts.SIGNAL_RECORDED_SUCCESS,reply_markup=signalist_menu())
                async with phase5_lock(callback):
                    if await _owned(state,SignalCreate.finalizing,flow,("final_validation_token",token),{"creation_token":creation_token,"canonical_symbol":canonical_symbol}):
                        await state.clear()
        finally:
            if claim is not None:
                phase5_coordination.remove_claim(claim)
        return
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
        async with phase5_lock(callback):
            current_data=await state.get_data()
            if (allowed.get(callback_data.ref) != await state.get_state()
                    or current_data.get("flow_token") != flow): return
            response,markup=await _back_state(state,current_data,callback_data.ref,flow)
        await callback.message.answer(response,reply_markup=markup)

async def _back_state(state,data,target,flow):
    if target=="shared": await state.set_state(SignalCreate.shared_search); await state.update_data(selected_exchange=None); return texts.ASK_SHARED_SYMBOL,signal_shared_navigation(flow)
    elif target=="exchange": await state.set_state(SignalCreate.exchange_selection); return texts.SIGNAL_SELECT_EXCHANGE,signal_exchanges(flow)
    elif target=="native-shared": await state.set_state(SignalCreate.shared_search); await state.update_data(selected_exchange=None,search_results=[]); return texts.ASK_SHARED_SYMBOL,signal_shared_navigation(flow)
    elif target=="symbol":
        if data.get("mode")=="single_exchange": await state.set_state(SignalCreate.native_search); return texts.SIGNAL_ASK_NATIVE,signal_native_navigation(flow)
        else: await state.set_state(SignalCreate.shared_search); return texts.ASK_SHARED_SYMBOL,signal_shared_navigation(flow)
    elif target=="direction": await state.set_state(SignalCreate.direction); return texts.SIGNAL_SELECT_DIRECTION,signal_directions(flow)
    elif target=="entry": await state.set_state(SignalCreate.entry); return texts.SIGNAL_ASK_ENTRY,signal_form_navigation(flow,"direction")
    elif target=="leverage": await state.set_state(SignalCreate.leverage); return texts.SIGNAL_ASK_LEVERAGE,signal_form_navigation(flow,"entry")
    elif target=="tp": await state.set_state(SignalCreate.take_profits); return texts.SIGNAL_ASK_TP,signal_tp_actions(flow,bool(data.get("take_profits")))
    elif target=="sl": await state.set_state(SignalCreate.stop_loss); return texts.SIGNAL_ASK_SL,signal_form_navigation(flow,"tp")

def _item(value): return {"canonical_symbol":value.canonical_symbol,"native_symbol":value.native_symbol,"base_asset":value.base_asset,"quote_asset":value.quote_asset,"current_price":str(value.current_price) if value.current_price is not None else None}

def _command(data):
    return CreateSignalCommand(signalist_id=data["signalist_id"] if "signalist_id" in data else data.get("actor_signalist_id",1),creation_token=data["creation_token"],mode=SignalMode(data["mode"]),canonical_symbol=data["canonical_symbol"],base_asset=data["base_asset"],quote_asset=data["quote_asset"],selected_exchange=Exchange(data["selected_exchange"]) if data.get("selected_exchange") else None,native_symbol=data.get("native_symbol"),direction=TradeDirection(data["direction"]),entry_price=Decimal(data["entry_price"]),leverage=int(data["leverage"]),take_profits=tuple(Decimal(x) for x in data["take_profits"]),stop_loss=Decimal(data["stop_loss"]))
