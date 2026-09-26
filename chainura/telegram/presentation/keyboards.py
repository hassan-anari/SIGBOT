from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton
from aiogram.types import ReplyKeyboardMarkup, ReplyKeyboardRemove

from chainura.telegram.presentation import texts
from chainura.telegram.presentation.callbacks import CallbackAction


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.MENU_ADMIN_SIGNALISTS), KeyboardButton(text=texts.MENU_ADMIN_DESTINATIONS)],
                  [KeyboardButton(text=texts.MENU_ADMIN_TEMPLATES), KeyboardButton(text=texts.MENU_ADMIN_REPORTS)],
                  [KeyboardButton(text=texts.MENU_ADMIN_MESSAGE_TEMPLATES)],
                  [KeyboardButton(text=texts.MENU_MAIN)],
                  [KeyboardButton(text=texts.MENU_ADMIN_STATUS), KeyboardButton(text=texts.MENU_HELP)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def signalist_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.MENU_CREATE_SIGNAL),KeyboardButton(text=texts.MENU_ACTIVE_SIGNALS)],
                  [KeyboardButton(text=texts.MENU_WARMUP)],
                  [KeyboardButton(text=texts.MENU_MAIN)],
                  [KeyboardButton(text=texts.MENU_SIGNALIST_STATUS), KeyboardButton(text=texts.MENU_HELP)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def remove_menu() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def status_navigation() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.BUTTON_REFRESH, callback_data=CallbackAction.REFRESH_STATUS.value),
        InlineKeyboardButton(text=texts.BUTTON_BACK, callback_data=CallbackAction.MENU.value),
    ]])


def signal_results(items, page: int, pages: int, flow: str, *, native: bool = False) -> InlineKeyboardMarkup:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    kind = "ns" if native else "ss"; page_kind = "np" if native else "sp"
    rows = [[InlineKeyboardButton(text=f"{item.native_symbol if native else item.canonical_symbol} — {item.current_price if item.current_price is not None else 'قیمت ناموجود'}",
             callback_data=signal_callback(kind, flow, str(index), page))]
            for index,item in enumerate(items)]
    nav=[]
    if page > 1: nav.append(InlineKeyboardButton(text=texts.BUTTON_PREVIOUS,callback_data=signal_callback(page_kind,flow,"-",page-1)))
    if page < pages: nav.append(InlineKeyboardButton(text=texts.BUTTON_NEXT,callback_data=signal_callback(page_kind,flow,"-",page+1)))
    if nav: rows.append(nav)
    if native:
        rows.extend(_native_navigation_rows(flow))
    else:
        rows.append([InlineKeyboardButton(text=texts.SIGNAL_SEARCH_OUTSIDE,callback_data=signal_callback("out",flow))])
        rows.append([InlineKeyboardButton(text=texts.SIGNAL_CANCEL,callback_data=signal_callback("cancel",flow))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def signal_exchanges(flow: str) -> InlineKeyboardMarkup:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=texts.EXCHANGE_NAMES[x],callback_data=signal_callback("ex",flow,x)) for x in ("ourbit","toobit","lbank")],
      [InlineKeyboardButton(text=texts.SIGNAL_BACK,callback_data=signal_callback("back",flow,"shared")),InlineKeyboardButton(text=texts.SIGNAL_CANCEL,callback_data=signal_callback("cancel",flow))]])


def signal_directions(flow: str) -> InlineKeyboardMarkup:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=texts.SIGNAL_LONG,callback_data=signal_callback("dir",flow,"long")),InlineKeyboardButton(text=texts.SIGNAL_SHORT,callback_data=signal_callback("dir",flow,"short"))],
      [InlineKeyboardButton(text=texts.SIGNAL_BACK,callback_data=signal_callback("back",flow,"symbol")),InlineKeyboardButton(text=texts.SIGNAL_CANCEL,callback_data=signal_callback("cancel",flow))]])


def signal_tp_actions(flow: str, has_tps: bool, *, details_only: bool = False) -> InlineKeyboardMarkup:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    rows=[[InlineKeyboardButton(text=texts.SIGNAL_TP_ADD,callback_data=signal_callback("tp",flow,"add"))]]
    if has_tps: rows.append([InlineKeyboardButton(text=texts.SIGNAL_TP_FINISH,callback_data=signal_callback("tp",flow,"finish"))])
    navigation=[]
    if not details_only:
        navigation.append(InlineKeyboardButton(text=texts.SIGNAL_BACK,callback_data=signal_callback("back",flow,"leverage")))
    navigation.append(InlineKeyboardButton(text=texts.SIGNAL_CANCEL,callback_data=signal_callback("cancel",flow)))
    rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def signal_review_actions(flow: str) -> InlineKeyboardMarkup:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=texts.SIGNAL_CONFIRM,callback_data=signal_callback("confirm",flow))],
      [InlineKeyboardButton(text=texts.SIGNAL_EDIT_DETAILS,callback_data=signal_callback("edit2",flow))],
      [InlineKeyboardButton(text=texts.SIGNAL_BACK,callback_data=signal_callback("back",flow,"sl")),InlineKeyboardButton(text=texts.SIGNAL_CANCEL,callback_data=signal_callback("cancel",flow))]])


def signal_stage_one_actions(flow: str) -> InlineKeyboardMarkup:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    return InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text=texts.SIGNAL_STAGE_ONE_CONFIRM,
                            callback_data=signal_callback("confirm1", flow))],
      [InlineKeyboardButton(text=texts.SIGNAL_STAGE_ONE_EDIT,
                            callback_data=signal_callback("edit1", flow)),
       InlineKeyboardButton(text=texts.SIGNAL_CANCEL,
                            callback_data=signal_callback("cancel", flow))]])


def signal_form_navigation(flow: str, back_target: str) -> InlineKeyboardMarkup:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.SIGNAL_BACK,callback_data=signal_callback("back",flow,back_target)),
        InlineKeyboardButton(text=texts.SIGNAL_CANCEL,callback_data=signal_callback("cancel",flow))]])


def signal_shared_navigation(flow: str) -> InlineKeyboardMarkup:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=texts.SIGNAL_SEARCH_OUTSIDE,
                              callback_data=signal_callback("out", flow))],
        [InlineKeyboardButton(text=texts.SIGNAL_CANCEL,
                              callback_data=signal_callback("cancel", flow))],
    ])


def _native_navigation_rows(flow: str) -> list[list[InlineKeyboardButton]]:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    return [
        [InlineKeyboardButton(text=texts.SIGNAL_BACK_EXCHANGE,
                              callback_data=signal_callback("back", flow, "exchange"))],
        [InlineKeyboardButton(text=texts.SIGNAL_BACK_SHARED,
                              callback_data=signal_callback("back", flow, "native-shared"))],
        [InlineKeyboardButton(text=texts.SIGNAL_CANCEL,
                              callback_data=signal_callback("cancel", flow))],
    ]


def signal_native_navigation(flow: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=_native_navigation_rows(flow))

def signal_continue_details(signal_id: int) -> InlineKeyboardMarkup:
    from chainura.telegram.presentation.signal_callbacks import signal_callback
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=texts.SIGNAL_CONTINUE_DETAILS,
            callback_data=signal_callback("continue","resume",str(signal_id)))
    ]])
