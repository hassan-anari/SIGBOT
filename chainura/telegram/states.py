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

class AdminTemplateManagement(StatesGroup):
    exchange = State()
    destination = State()
    upload_default = State()
    override = State()
    upload_override = State()
    remove_confirm = State()

class AdminMessageTemplateManagement(StatesGroup):
    browsing = State()
    editing = State()
    confirming = State()


class SignalCreate(StatesGroup):
    order_type = State()
    shared_search = State()
    exchange_selection = State()
    native_search = State()
    direction = State()
    limit_entry = State()
    leverage = State()
    stage_one_review = State()
    stage_one_creating = State()
    take_profits = State()
    stop_loss = State()
    review = State()
    finalizing = State()

class SignalComment(StatesGroup):
    waiting_text = State()
    waiting_snapshot_choice = State()

class AdminReportFlow(StatesGroup):
    start_date = State()
    end_date = State()
