from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from chainura.domain.monitoring import CandidateStatus
from chainura.telegram.presentation import texts
from chainura.telegram.middlewares import CallbackAcknowledger

def event_decision_keyboard(candidate_id:int)->InlineKeyboardMarkup:
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
