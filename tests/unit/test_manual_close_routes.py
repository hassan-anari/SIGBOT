from datetime import datetime,timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import unittest
from chainura.application.active_signals import ActiveSignalView
from chainura.application.monitoring import ManualClosePriceUnavailable
from chainura.domain.monitoring import MonitorStage
from chainura.domain.signal import SignalLifecycleStatus,TradeDirection
from chainura.telegram import active_signal_routes as routes
from chainura.telegram.presentation import texts
from tests.unit.test_phase7_report_routes import State,Message,active_actor

class ManualCloseRouteTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.state=State();self.actor=active_actor()
  self.view=ActiveSignalView(9,'BTCUSDT',TradeDirection.LONG,Decimal(100),2,SignalLifecycleStatus.ACTIVE,(Decimal(110),),Decimal(90),MonitorStage.PRE_TP1,0,datetime.now(timezone.utc),position_version=3)
  self.active=SimpleNamespace(get_open=AsyncMock(return_value=self.view))
  self.monitor=SimpleNamespace(manual_close_version=AsyncMock(return_value=3),manual_full_close=AsyncMock(return_value=True))
 async def call(self,data):
  query=SimpleNamespace(data=data,message=Message())
  with patch.object(routes,'Message',Message),patch('chainura.telegram.signal_routes.reconcile_stage_one_creating',AsyncMock(return_value=None)):
   await routes.active_callback(query,self.actor,self.state,SimpleNamespace(answer_once=AsyncMock()),self.active,None,monitoring_service=self.monitor)
  return query.message
 async def test_panel_requires_identity_confirmation_and_uses_authenticated_owner(self):
  message=await self.call(routes.cb('f',9,1))
  self.assertIn('TR-',message.answers[0][0]);self.assertIn('BTCUSDT',message.answers[0][0])
  self.monitor.manual_full_close.assert_not_awaited()
  token=(await self.state.get_data())['full_close_token']
  message=await self.call(routes.cb('z',9,token))
  self.assertEqual(self.monitor.manual_full_close.await_args.args,(9,self.actor.signalist.id,3))
  self.assertIn("confirmation_current",self.monitor.manual_full_close.await_args.kwargs)
  self.assertEqual(message.answers[0][0],texts.FULL_CLOSE_APPLIED)
  await self.call(routes.cb('z',9,token));self.assertEqual(self.monitor.manual_full_close.await_count,1)
 async def test_unconfirmed_or_wrong_token_cannot_close(self):
  await self.call(routes.cb('z',9,123));self.monitor.manual_full_close.assert_not_awaited()
  await self.call(routes.cb('f',9,1));await self.call(routes.cb('z',9,-1));self.monitor.manual_full_close.assert_not_awaited()
 async def test_price_failure_preserves_confirmation_and_safe_response(self):
  await self.call(routes.cb('f',9,1));token=(await self.state.get_data())['full_close_token']
  self.monitor.manual_full_close.side_effect=ManualClosePriceUnavailable()
  message=await self.call(routes.cb('z',9,token))
  self.assertEqual(message.answers[0][0],texts.FULL_CLOSE_PRICE_UNAVAILABLE)
  self.assertEqual((await self.state.get_data())['full_close_token'],token)
 async def test_no_owned_open_trade_returns_stale_response(self):
  self.active.get_open.return_value=None
  message=await self.call(routes.cb('f',9,1));self.monitor.manual_close_version.assert_not_awaited()
  self.assertEqual(message.answers[0][0],texts.ACTIVE_SIGNAL_STALE)

 async def test_declining_confirmation_invalidates_old_yes_callback(self):
  await self.call(routes.cb('f',9,1));token=(await self.state.get_data())['full_close_token']
  await self.call(routes.cb('d',9,1));await self.call(routes.cb('z',9,token))
  self.monitor.manual_full_close.assert_not_awaited()
