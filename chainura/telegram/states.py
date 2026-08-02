from aiogram.fsm.state import State, StatesGroup


class AdminSignalistCreate(StatesGroup):
    telegram_user_id = State()
    display_name = State()
    review = State()


class AdminDestinationCreate(StatesGroup):
    telegram_chat_id = State()
    discovering = State()
    candidate_review = State()
    access_level = State()
    daily_limit = State()
    priority = State()
    review = State()


class AdminDestinationEdit(StatesGroup):
    access_level = State()
    daily_limit = State()
    review = State()


class AdminDestinationPriority(StatesGroup):
    editing = State()
    review = State()


class AdminSignalistArchive(StatesGroup):
    confirming = State()


class SignalCreate(StatesGroup):
    shared_search = State()
    exchange_selection = State()
    native_search = State()
    direction = State()
    entry = State()
    leverage = State()
    take_profits = State()
    stop_loss = State()
    review = State()
    finalizing = State()
