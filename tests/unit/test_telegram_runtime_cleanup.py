import unittest
from unittest.mock import AsyncMock
from chainura.config import ApplicationSettings, TelegramRuntimeSettings
from chainura.telegram.runtime import Runtime

class Session:
    def __init__(self,order): self.close=AsyncMock(side_effect=lambda:order.append('bot'))
class Bot:
    def __init__(self,order): self.session=Session(order)
class Storage:
    def __init__(self,order): self.close=AsyncMock(side_effect=lambda:order.append('storage'))
class Lease:
    def __init__(self,order): self.release=AsyncMock(side_effect=lambda:order.append('lease'))
class Engine:
    def __init__(self,order): self.dispose=AsyncMock(side_effect=lambda:order.append('engine'))

class RuntimeCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_storage_bot_engine_exactly_once(self):
        order=[]; settings=TelegramRuntimeSettings(ApplicationSettings(
            'postgresql+psycopg://u:p@localhost/db',1),'token','redis://localhost/0')
        runtime=Runtime(settings); runtime.lease=Lease(order); runtime.storage=Storage(order)
        runtime.bot=Bot(order); runtime.engine=Engine(order)
        await runtime.cleanup(); await runtime.cleanup()
        self.assertEqual(order,['lease','storage','bot','engine'])
        runtime.lease.release.assert_awaited_once(); runtime.storage.close.assert_awaited_once()
        runtime.bot.session.close.assert_awaited_once(); runtime.engine.dispose.assert_awaited_once()
