from aiogram.fsm.state import State, StatesGroup
class AdminSignalistCreate(StatesGroup):
    telegram_user_id=State(); display_name=State(); telegram_username=State(); review=State()
class AdminDestinationCreate(StatesGroup):
    internal_name=State(); telegram_chat_id=State(); destination_type=State(); access_level=State(); daily_limit=State(); priority=State(); review=State()
class AdminDestinationEdit(StatesGroup):
    internal_name=State(); destination_type=State(); access_level=State(); daily_limit=State(); review=State()
class AdminDestinationPriority(StatesGroup):
    editing=State(); review=State()
class AdminSignalistArchive(StatesGroup):
    confirming=State()
