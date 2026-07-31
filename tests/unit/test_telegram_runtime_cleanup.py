import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from chainura.config import ApplicationSettings, TelegramRuntimeSettings
from chainura.errors import TelegramRuntimeError
from chainura.telegram.runtime import Runtime, RuntimeState
from chainura.telegram.redis_runtime import PollerLease


def settings():
    return TelegramRuntimeSettings(ApplicationSettings(
        'postgresql+psycopg://u:p@localhost/db',1),'123456:token','redis://localhost/0')

class Session:
    def __init__(self,order,error=None):
        async def close():
            order.append('bot')
            if error: raise error
        self.close=AsyncMock(side_effect=close)
class Bot:
    def __init__(self,order,error=None): self.session=Session(order,error)
class Storage:
    def __init__(self,order,error=None):
        async def close():
            order.append('storage')
            if error: raise error
        self.close=AsyncMock(side_effect=close)
class Lease:
    def __init__(self,order,error=None):
        async def release():
            order.append('lease')
            if error: raise error
            return True
        self.release=AsyncMock(side_effect=release)
class Engine:
    def __init__(self,order,error=None):
        async def dispose():
            order.append('engine')
            if error: raise error
        self.dispose=AsyncMock(side_effect=dispose)

class RuntimeCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_storage_bot_engine_exactly_once(self):
        order=[]; runtime=Runtime(settings()); runtime.lease=Lease(order); runtime.storage=Storage(order)
        runtime.bot=Bot(order); runtime.engine=Engine(order)
        await runtime.cleanup(); await runtime.cleanup()
        self.assertEqual(order,['lease','storage','bot','engine'])
        runtime.lease.release.assert_awaited_once(); runtime.storage.close.assert_awaited_once()
        runtime.bot.session.close.assert_awaited_once(); runtime.engine.dispose.assert_awaited_once()
        self.assertIs(runtime.state,RuntimeState.STOPPED)

    async def test_each_cleanup_failure_does_not_block_later_resources(self):
        for failed in ('lease','storage','bot'):
            order=[]; runtime=Runtime(settings())
            runtime.lease=Lease(order,RuntimeError('failed') if failed=='lease' else None)
            runtime.storage=Storage(order,RuntimeError('failed') if failed=='storage' else None)
            runtime.bot=Bot(order,RuntimeError('failed') if failed=='bot' else None)
            runtime.engine=Engine(order)
            with self.subTest(failed=failed):
                await runtime.cleanup()
                self.assertEqual(order,['lease','storage','bot','engine'])
                self.assertIs(runtime.state,RuntimeState.STOPPED)

    async def test_failed_renewal_task_does_not_abort_cleanup(self):
        order=[]; runtime=Runtime(settings()); runtime.lease=Lease(order); runtime.storage=Storage(order)
        runtime.bot=Bot(order); runtime.engine=Engine(order)
        async def fail(): raise TelegramRuntimeError('renewal failed')
        runtime.renewal_task=asyncio.create_task(fail())
        await asyncio.sleep(0)
        await runtime.cleanup()
        self.assertEqual(order,['lease','storage','bot','engine'])

    async def test_unexpected_renewal_completion_requests_shutdown(self):
        runtime=Runtime(settings())
        runtime.renewal_task=asyncio.create_task(asyncio.sleep(0))
        runtime.polling_task=asyncio.create_task(asyncio.sleep(30))
        with self.assertRaises(TelegramRuntimeError):
            await runtime._supervise()
        self.assertTrue(runtime.shutdown_requested.is_set())
        runtime.polling_task.cancel()
        with self.assertRaises(asyncio.CancelledError): await runtime.polling_task

    async def test_each_renewal_failure_stops_polling_and_cleans_up(self):
        for failure in (False, TelegramRuntimeError('safe'), RuntimeError('unexpected')):
            order=[]; runtime=Runtime(settings()); runtime.state=RuntimeState.RUNNING
            lease=PollerLease(object(),'identity')
            lease.renew=AsyncMock(return_value=failure) if failure is False else AsyncMock(side_effect=failure)
            runtime.lease=lease; lease.release=Lease(order).release
            runtime.storage=Storage(order); runtime.bot=Bot(order); runtime.engine=Engine(order)
            class Dispatcher:
                async def stop_polling(self):
                    order.append('stop-polling'); runtime.polling_task.cancel()
            runtime.dispatcher=Dispatcher()
            runtime.polling_task=asyncio.create_task(asyncio.sleep(30))
            runtime.renewal_task=asyncio.create_task(lease.run_renewal(
                runtime.renewal_stop,runtime.lease_lost))
            with self.subTest(failure=type(failure).__name__), patch(
                'chainura.telegram.redis_runtime.LEASE_RENEW_SECONDS',0
            ), self.assertRaises(TelegramRuntimeError):
                await runtime._supervise()
            await runtime.cleanup()
            self.assertTrue(runtime.shutdown_requested.is_set())
            self.assertEqual(order,['stop-polling','lease','storage','bot','engine'])

    async def test_shutdown_before_start_prevents_resource_construction(self):
        runtime=Runtime(settings()); runtime.request_shutdown(); await runtime.start()
        self.assertIsNone(runtime.redis); self.assertIsNone(runtime.dispatcher)
        self.assertIsNone(runtime.polling_task); self.assertIs(runtime.state,RuntimeState.STOPPED)

    async def test_renewal_starts_before_slow_telegram_startup(self):
        order=[]
        class Redis:
            async def ping(self): order.append('ping')
            async def aclose(self,**kwargs): order.append('redis')
        class RenewingLease:
            def __init__(self,*args,**kwargs): self.renewals=0
            async def acquire(self): order.append('acquire'); return True
            async def run_renewal(self,stop,lost):
                order.append('renewal-start')
                while not stop.is_set():
                    self.renewals += 1
                    try: await asyncio.wait_for(stop.wait(),.001)
                    except asyncio.TimeoutError: pass
            async def release(self): order.append('lease'); return True
        class SlowBot:
            def __init__(self,*args,**kwargs): self.session=Session(order)
            async def set_my_commands(self,*args):
                order.append('commands-start'); await asyncio.sleep(.02); order.append('commands-end')
            async def delete_webhook(self,**kwargs):
                order.append('webhook'); runtime.request_shutdown(); await asyncio.sleep(0)
        class FakeStorage:
            async def close(self): order.append('storage')
        class FakeEngine:
            async def dispose(self): order.append('engine')
        runtime=Runtime(settings()); redis=Redis(); lease=RenewingLease()
        with patch('chainura.telegram.runtime.create_database_engine',return_value=FakeEngine()), \
             patch('chainura.telegram.runtime.create_session_factory',return_value=lambda:None), \
             patch('chainura.telegram.runtime.Redis.from_url',return_value=redis), \
             patch('chainura.telegram.runtime.PollerLease',return_value=lease), \
             patch('chainura.telegram.runtime.Bot',SlowBot), \
             patch('chainura.telegram.runtime.create_production_storage',return_value=FakeStorage()), \
             patch('chainura.telegram.runtime.build_dispatcher',return_value=object()):
            await runtime.start()
        self.assertLess(order.index('renewal-start'),order.index('commands-start'))
        self.assertGreater(lease.renewals,1)
        self.assertNotIn('polling',order)
        self.assertEqual(order.count('lease'),1); self.assertEqual(order.count('storage'),1)
