import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from chainura.config import ApplicationSettings, TelegramRuntimeSettings
from chainura.errors import TelegramRuntimeError
from chainura.telegram.runtime import Runtime, RuntimeState
from chainura.telegram.redis_runtime import PollerLease


def settings():
    return TelegramRuntimeSettings(ApplicationSettings(
        'sqlite+aiosqlite:///./data/test.db',(1,),1),'123456:token','redis://localhost/0')

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

    async def test_each_hanging_cleanup_operation_is_bounded(self):
        for hanging in ('lease','storage','bot','engine'):
            order=[]; never=asyncio.Event(); runtime=Runtime(settings())
            lease=Lease(order); storage=Storage(order); bot=Bot(order); engine=Engine(order)
            async def hang(name):
                order.append(name)
                await never.wait()
            def hanging_action(name):
                async def action():
                    await hang(name)
                return action
            if hanging=='lease': lease.release=AsyncMock(side_effect=hanging_action('lease'))
            if hanging=='storage': storage.close=AsyncMock(side_effect=hanging_action('storage'))
            if hanging=='bot': bot.session.close=AsyncMock(side_effect=hanging_action('bot'))
            if hanging=='engine': engine.dispose=AsyncMock(side_effect=hanging_action('engine'))
            runtime.lease=lease; runtime.storage=storage; runtime.bot=bot; runtime.engine=engine
            with self.subTest(hanging=hanging), patch(
                'chainura.telegram.runtime.CLEANUP_OPERATION_TIMEOUT_SECONDS',.01
            ):
                await runtime.cleanup()
                self.assertEqual(order,['lease','storage','bot','engine'])
                self.assertIs(runtime.state,RuntimeState.STOPPED)
                self.assertEqual(len([task for task in asyncio.all_tasks()
                    if task is not asyncio.current_task()]),0)

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
             patch('chainura.telegram.runtime.initialize_database_engine',new=AsyncMock()), \
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

    async def test_renewal_failure_during_telegram_startup_is_fatal(self):
        for stage in ('commands','webhook'):
            order=[]; trigger=asyncio.Event(); never=asyncio.Event()
            class Redis:
                async def ping(self): pass
            class FailingLease:
                async def acquire(self): return True
                async def run_renewal(self,stop,lost):
                    await trigger.wait(); lost.set(); raise TelegramRuntimeError('safe renewal failure')
                async def release(self): order.append('lease'); return True
            class StartupBot:
                def __init__(self,*args,**kwargs): self.session=Session(order)
                async def set_my_commands(self,*args):
                    if stage=='commands': trigger.set(); await never.wait()
                async def delete_webhook(self,**kwargs):
                    if stage=='webhook': trigger.set(); await never.wait()
            class FakeStorage:
                async def close(self): order.append('storage')
            class FakeEngine:
                async def dispose(self): order.append('engine')
            runtime=Runtime(settings())
            with self.subTest(stage=stage), patch(
                'chainura.telegram.runtime.create_database_engine',return_value=FakeEngine()
            ), patch('chainura.telegram.runtime.initialize_database_engine',new=AsyncMock()), patch('chainura.telegram.runtime.create_session_factory',return_value=lambda:None), \
                 patch('chainura.telegram.runtime.Redis.from_url',return_value=Redis()), \
                 patch('chainura.telegram.runtime.PollerLease',return_value=FailingLease()), \
                 patch('chainura.telegram.runtime.Bot',StartupBot), \
                 patch('chainura.telegram.runtime.create_production_storage',return_value=FakeStorage()), \
                 patch('chainura.telegram.runtime.build_dispatcher',return_value=object()), \
                 self.assertRaises(TelegramRuntimeError):
                await runtime.start()
            self.assertIsNone(runtime.polling_task)
            self.assertEqual(order,['lease','storage','bot','engine'])
            self.assertIs(runtime.state,RuntimeState.STOPPED)

    async def test_renewal_timeout_during_command_registration_is_fatal(self):
        order=[]; never=asyncio.Event()
        class Redis:
            async def ping(self): pass
        lease=PollerLease(object(),'identity')
        lease.acquire=AsyncMock(return_value=True)
        lease.renew=AsyncMock(side_effect=never.wait)
        lease.release=Lease(order).release
        class StartupBot:
            def __init__(self,*args,**kwargs): self.session=Session(order)
            async def set_my_commands(self,*args): await never.wait()
            async def delete_webhook(self,**kwargs): self.fail('must not run')
        class FakeStorage:
            async def close(self): order.append('storage')
        class FakeEngine:
            async def dispose(self): order.append('engine')
        runtime=Runtime(settings())
        with patch('chainura.telegram.runtime.create_database_engine',return_value=FakeEngine()), \
             patch('chainura.telegram.runtime.initialize_database_engine',new=AsyncMock()), \
             patch('chainura.telegram.runtime.create_session_factory',return_value=lambda:None), \
             patch('chainura.telegram.runtime.Redis.from_url',return_value=Redis()), \
             patch('chainura.telegram.runtime.PollerLease',return_value=lease), \
             patch('chainura.telegram.runtime.Bot',StartupBot), \
             patch('chainura.telegram.runtime.create_production_storage',return_value=FakeStorage()), \
             patch('chainura.telegram.runtime.build_dispatcher',return_value=object()), \
             patch('chainura.telegram.redis_runtime.LEASE_RENEW_SECONDS',0), \
             patch('chainura.telegram.redis_runtime.RENEW_COMMAND_TIMEOUT_SECONDS',.01), \
             self.assertRaises(TelegramRuntimeError):
            await runtime.start()
        self.assertIsNone(runtime.polling_task)
        self.assertEqual(order,['lease','storage','bot','engine'])
        self.assertTrue(runtime.lease_lost.is_set())

class RuntimePhase6BCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_publication_task_is_supervised(self):
        runtime=Runtime(settings());runtime.state=RuntimeState.RUNNING
        runtime.renewal_task=asyncio.create_task(asyncio.sleep(30));runtime.polling_task=asyncio.create_task(asyncio.sleep(30))
        async def fail(): raise RuntimeError('boom')
        runtime.update_publication_task=asyncio.create_task(fail())
        await asyncio.sleep(0)
        with self.assertRaises(TelegramRuntimeError):await runtime._supervise()
        self.assertTrue(runtime.shutdown_requested.is_set())
        for task in (runtime.renewal_task,runtime.polling_task):
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
    async def test_position_action_worker_is_supervised_and_closed(self):
        runtime=Runtime(settings());runtime.state=RuntimeState.RUNNING
        runtime.renewal_task=asyncio.create_task(asyncio.sleep(30));runtime.polling_task=asyncio.create_task(asyncio.sleep(30))
        async def fail():raise RuntimeError('boom')
        runtime.position_action_publication_task=asyncio.create_task(fail())
        await asyncio.sleep(0)
        with self.assertRaises(TelegramRuntimeError):await runtime._supervise()
        self.assertTrue(runtime.shutdown_requested.is_set())
        for task in (runtime.renewal_task,runtime.polling_task):
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        class Worker:
            def __init__(self):self.closed=False
            async def close(self):self.closed=True
        cleanup_runtime=Runtime(settings());worker=Worker();cleanup_runtime.position_action_publication_worker=worker
        await cleanup_runtime.cleanup();self.assertTrue(worker.closed)
    def test_notification_wiring_is_not_nested_under_update_worker_condition(self):
        import inspect
        source=inspect.getsource(Runtime.start)
        self.assertIn('monitoring_service.notification_wake=self.notification_worker.wake',source)
        self.assertIn('monitoring_service.update_publication_wake=self.update_publication_worker.wake',source)
        self.assertNotIn('if self.update_publication_worker: monitoring_service.update_publication_wake=self.update_publication_worker.wake;await self._startup_await(self.notification_worker.start())',source)


class RuntimeOrphanCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_owned_initial_and_update_outputs_only(self):
        import tempfile
        from pathlib import Path
        from dataclasses import replace
        from chainura.rendering.production import RenderingSettings
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);out=root/'out';out.mkdir();assets=root/'templates';assets.mkdir();logos=root/'logos';logos.mkdir();font=root/'font.otf';font.write_text('font')
            settings_obj=settings();settings_obj=replace(settings_obj,rendering=RenderingSettings(assets,out,logos,font,font,font,font,1))
            runtime=Runtime(settings_obj)
            for name in ('initial-a.png','.initial-a.png.worker.tmp','update-b.png','.update-b.png.worker.tmp','other.png'):
                (out/name).write_text('x')
            nested=out/'nested';nested.mkdir();(nested/'update-c.png').write_text('x')
            (assets/'update-template.png').write_text('x');(logos/'update-logo.png').write_text('x')
            await runtime._cleanup_orphan_outputs();await runtime._cleanup_orphan_outputs()
            self.assertFalse((out/'initial-a.png').exists());self.assertFalse((out/'.initial-a.png.worker.tmp').exists())
            self.assertFalse((out/'update-b.png').exists());self.assertFalse((out/'.update-b.png.worker.tmp').exists())
            self.assertTrue((out/'other.png').exists());self.assertTrue((nested/'update-c.png').exists())
            self.assertTrue((assets/'update-template.png').exists());self.assertTrue((logos/'update-logo.png').exists());self.assertTrue(font.exists())
