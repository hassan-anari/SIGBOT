from aiogram import F, Router
from aiogram.types import Message

from chainura.application.access import Actor, ActorKind
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import remove_menu

router = Router(name="phase2-warmup")


@router.message(F.text == texts.MENU_WARMUP)
async def warmup(message: Message, actor: Actor, warmup_service):
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST or actor.signalist is None:
        denial = {ActorKind.INACTIVE_SIGNALIST: texts.INACTIVE,
                  ActorKind.ARCHIVED_SIGNALIST: texts.ARCHIVED}.get(actor.kind, texts.UNKNOWN)
        await message.answer(denial, reply_markup=remove_menu())
        return
    try:
        await warmup_service.trigger(actor.signalist.id)
    except PermissionError:
        await message.answer(texts.INACTIVE, reply_markup=remove_menu())
    except Exception:
        await message.answer(texts.TEMPORARY_ERROR)
    else:
        await message.answer(texts.WARMUP_ACCEPTED)
