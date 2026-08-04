from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery
from chainura.domain.monitoring import CandidateStatus
from chainura.telegram.presentation import texts

router=Router(name="phase6a_events")
class EventDecisionCallback(CallbackData,prefix="e6"):
    action:str
    candidate_id:int

@router.callback_query(EventDecisionCallback.filter())
async def event_decision(callback:CallbackQuery,callback_data:EventDecisionCallback,actor,monitoring_service):
    await callback.answer()
    if actor.signalist is None:return
    try:status=await monitoring_service.decide_candidate(callback_data.candidate_id,actor.signalist.id,callback_data.action=="a")
    except (ValueError,TypeError):
        await callback.message.answer(texts.EVENT_DECISION_STALE);return
    await callback.message.answer(texts.EVENT_APPROVED if status is CandidateStatus.APPROVED else texts.EVENT_DECLINED)
