from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from uuid import uuid4

from chainura.application.access import Actor, ActorKind
from chainura.telegram.middlewares import CallbackAcknowledger
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.callbacks import CallbackAction, parse_callback
from chainura.telegram.presentation.keyboards import admin_menu, remove_menu, signalist_menu, status_navigation
from chainura.telegram.states import SignalCreate

router = Router(name="phase3-shell")


def denial(actor: Actor) -> str:
    return {
        ActorKind.INACTIVE_SIGNALIST: texts.INACTIVE,
        ActorKind.ARCHIVED_SIGNALIST: texts.ARCHIVED,
        ActorKind.UNKNOWN: texts.UNKNOWN,
    }[actor.kind]


async def deny_message(message: Message, actor: Actor) -> None:
    await message.answer(denial(actor), reply_markup=remove_menu())


async def authorize_message(message: Message, actor: Actor) -> bool:
    if actor.authorized:
        return True
    await deny_message(message, actor)
    return False


async def authorize_callback(callback: CallbackQuery, actor: Actor,
                             acknowledger: CallbackAcknowledger) -> bool:
    if actor.authorized:
        return True
    await acknowledger.answer_once(denial(actor))
    if isinstance(callback.message, Message):
        await callback.message.answer(denial(actor), reply_markup=remove_menu())
    return False


async def show_menu(message: Message, actor: Actor, *, welcome: bool = False) -> None:
    if actor.kind is ActorKind.MAIN_ADMIN:
        await message.answer(texts.WELCOME_ADMIN if welcome else texts.DASHBOARD_ADMIN,
                             reply_markup=admin_menu())
    elif actor.kind is ActorKind.ACTIVE_SIGNALIST and actor.signalist:
        content = texts.welcome_signalist(actor.signalist.display_name) if welcome else texts.DASHBOARD_SIGNALIST
        await message.answer(content, reply_markup=signalist_menu())
    else:
        await deny_message(message, actor)


async def show_status(message: Message, actor: Actor) -> None:
    if actor.kind is ActorKind.MAIN_ADMIN:
        content = texts.STATUS_ADMIN
    elif actor.kind is ActorKind.ACTIVE_SIGNALIST and actor.signalist:
        content = texts.status_signalist(actor.signalist.display_name)
    else:
        await deny_message(message, actor)
        return
    await message.answer(content, reply_markup=status_navigation())


@router.message(Command("start"))
async def start(message: Message, actor: Actor) -> None:
    await show_menu(message, actor, welcome=True)


@router.message(Command("help"))
@router.message(F.text == texts.MENU_HELP)
async def help_handler(message: Message, actor: Actor) -> None:
    if actor.kind is ActorKind.MAIN_ADMIN:
        await message.answer(texts.HELP_ADMIN, reply_markup=admin_menu())
    elif actor.kind is ActorKind.ACTIVE_SIGNALIST:
        await message.answer(texts.HELP_SIGNALIST, reply_markup=signalist_menu())
    else:
        await deny_message(message, actor)


@router.message(F.text == texts.MENU_MAIN)
async def menu_handler(message: Message, actor: Actor) -> None:
    if await authorize_message(message, actor):
        await show_menu(message, actor)


@router.message(F.text.in_({texts.MENU_ADMIN_STATUS, texts.MENU_SIGNALIST_STATUS}))
async def status_handler(message: Message, actor: Actor) -> None:
    if await authorize_message(message, actor):
        await show_status(message, actor)


@router.message(F.text == texts.MENU_CREATE_SIGNAL)
async def create_signal_start(message: Message, actor: Actor, state: FSMContext) -> None:
    if actor.kind is not ActorKind.ACTIVE_SIGNALIST:
        await deny_message(message, actor)
        return
    await state.clear()
    await state.set_state(SignalCreate.shared_search)
    await state.set_data({"flow_token": uuid4().hex, "creation_token": uuid4().hex})
    await message.answer(texts.ASK_SHARED_SYMBOL)


@router.callback_query()
async def callback_handler(callback: CallbackQuery, actor: Actor,
                           callback_ack: CallbackAcknowledger) -> None:
    if not await authorize_callback(callback, actor, callback_ack):
        return
    action = parse_callback(callback.data)
    if action is None:
        await callback_ack.answer_once(texts.INVALID_CALLBACK)
        return
    await callback_ack.answer_once()
    if not isinstance(callback.message, Message):
        return
    if action is CallbackAction.MENU:
        await show_menu(callback.message, actor)
    else:
        await show_status(callback.message, actor)


@router.message()
async def fallback(message: Message, actor: Actor) -> None:
    if actor.authorized:
        await message.answer(texts.FALLBACK)
    else:
        await deny_message(message, actor)
