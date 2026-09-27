from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from chainura.domain.monitoring import CandidateStatus
from chainura.domain.position import PositionActionKind
from chainura.telegram.presentation import texts
from chainura.telegram.middlewares import CallbackAcknowledger

class TpActionCallback(CallbackData,prefix="p4b"):
    action:str
    candidate_id:int

def tp_action_keyboard(candidate_id:int,actions)->InlineKeyboardMarkup:
    labels={PositionActionKind.NORMAL_TP:texts.TP_ACTION_NORMAL_BUTTON,
        PositionActionKind.RISK_FREE:texts.TP_ACTION_RISK_FREE_BUTTON,
        PositionActionKind.SAVE_PROFIT_50:texts.TP_ACTION_SAVE50_BUTTON,
        PositionActionKind.FULL_CLOSE:texts.TP_ACTION_FULL_CLOSE_BUTTON}
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=labels[action],
        callback_data=TpActionCallback(action=action.value,candidate_id=candidate_id).pack())]
        for action in actions])

def event_decision_keyboard(candidate_id:int,actions=None)->InlineKeyboardMarkup:
    if actions:return tp_action_keyboard(candidate_id,actions)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.EVENT_PUBLISH_BUTTON,callback_data=EventDecisionCallback(action="a",candidate_id=candidate_id).pack()),
        InlineKeyboardButton(text=texts.EVENT_DECLINE_BUTTON,callback_data=EventDecisionCallback(action="d",candidate_id=candidate_id).pack())]])

def breakeven_prompt_keyboard(prompt_id:int,cycle:int=0)->InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BREAKEVEN_ACCEPT_BUTTON,callback_data=BreakevenPromptCallback(action="a",prompt_id=prompt_id,cycle=cycle).pack()),
        InlineKeyboardButton(text=texts.BREAKEVEN_DECLINE_BUTTON,callback_data=BreakevenPromptCallback(action="d",prompt_id=prompt_id,cycle=cycle).pack())]])

router=Router(name="phase6a_events")
class EventDecisionCallback(CallbackData,prefix="e6"):
    action:str
    candidate_id:int

class BreakevenPromptCallback(CallbackData,prefix="b6"):
    action:str
    prompt_id:int
    cycle:int=0

@router.callback_query(TpActionCallback.filter())
async def tp_action_decision(callback:CallbackQuery,callback_data:TpActionCallback,actor,monitoring_service,callback_ack:CallbackAcknowledger):
    await callback_ack.answer_once()
    if actor.signalist is None:return
    try:
        await monitoring_service.decide_tp_action(callback_data.candidate_id,actor.signalist.id,
            PositionActionKind(callback_data.action))
        text=texts.TP_ACTION_APPLIED
    except (ValueError,TypeError):text=texts.EVENT_DECISION_STALE
    if callback.message is not None:await callback.message.answer(text)

@router.callback_query(EventDecisionCallback.filter())
async def event_decision(callback:CallbackQuery,callback_data:EventDecisionCallback,actor,monitoring_service,callback_ack:CallbackAcknowledger):
    await callback_ack.answer_once()
    if actor.signalist is None:return
    try:status=await monitoring_service.decide_candidate(callback_data.candidate_id,actor.signalist.id,callback_data.action=="a")
    except (ValueError,TypeError):
        await callback.message.answer(texts.EVENT_DECISION_STALE);return
    await callback.message.answer(texts.EVENT_APPROVED if status is CandidateStatus.APPROVED else texts.EVENT_DECLINED)


@router.callback_query(BreakevenPromptCallback.filter())
async def breakeven_prompt_decision(callback:CallbackQuery,callback_data:BreakevenPromptCallback,actor,monitoring_service,callback_ack:CallbackAcknowledger):
    await callback_ack.answer_once()
    if actor.signalist is None:
        return
    try:
        if callback_data.action == "d":
            await monitoring_service.decline_breakeven(callback_data.prompt_id,actor.signalist.id,callback_data.cycle)
            text=texts.BREAKEVEN_DECLINED
        elif callback_data.action == "a":
            registered=await monitoring_service.register_breakeven_with_fresh_price(callback_data.prompt_id,actor.signalist.id,callback_data.cycle)
            text=texts.BREAKEVEN_REGISTERED if registered else texts.BREAKEVEN_OUT_OF_RANGE
        else:
            text=texts.BREAKEVEN_STALE
    except (ValueError,TypeError):
        text=texts.BREAKEVEN_STALE
    if callback.message is not None and hasattr(callback.message,"answer"):
        await callback.message.answer(text)
