import unittest
from datetime import datetime,timezone

from chainura.application.access import Actor,ActorKind
from chainura.application.message_templates import BUILTIN_TEMPLATES
from chainura.application.pagination import Page
from chainura.domain.destination import Destination,DestinationAccessLevel,DestinationStatus,DestinationType
from chainura.domain.message_template import DestinationMessageKind,MessageTemplateSource
from chainura.telegram.message_template_routes import callbacks,open_menu,receive_text
from chainura.telegram.presentation import texts

NOW=datetime(2026,8,9,tzinfo=timezone.utc)

class State:
 def __init__(self):self.data={};self.state=None
 async def clear(self):self.data={};self.state=None
 async def update_data(self,**values):self.data.update(values)
 async def set_state(self,value):self.state=value.state if hasattr(value,"state") else value
 async def get_data(self):return dict(self.data)
 async def get_state(self):return self.state
class Message:
 def __init__(self,text=None):self.text=text;self.answers=[]
 async def answer(self,text,reply_markup=None):self.answers.append((text,reply_markup))
class Query:
 def __init__(self,data,message):self.data=data;self.message=message
class Ack:
 async def answer_once(self,*args):pass
class Destinations:
 def __init__(self):
  self.items=[Destination.rehydrate(id=i,internal_name=f"D{i}",telegram_chat_id=-i,
   destination_type=DestinationType.CHANNEL,access_level=DestinationAccessLevel.PUBLIC,
   status=DestinationStatus.ACTIVE,daily_limit=5,created_at=NOW,updated_at=NOW) for i in range(1,10)]
 async def list_page(self,status,request):
  start=(request.page-1)*8;return Page(tuple(self.items[start:start+8]),request.page,8,len(self.items))
 async def get(self,id):return self.items[id-1]
class Templates:
 def __init__(self):self.rows={}
 async def effective_template(self,kind,destination_id=None):
  return self.rows.get((kind,destination_id),BUILTIN_TEMPLATES[kind]),(MessageTemplateSource.DESTINATION if (kind,destination_id) in self.rows else MessageTemplateSource.BUILTIN)
 def preview(self,kind,text):return text.replace("{symbol}","BTCUSDT")
 async def set_destination(self,kind,destination_id,text):self.rows[kind,destination_id]=text
 async def set_global(self,kind,text):self.rows[kind,None]=text
 async def remove_destination(self,*args):pass
 async def remove_global(self,*args):pass

class MessageTemplateRouteTests(unittest.IsolatedAsyncioTestCase):
 async def test_page_two_destination_can_be_selected_and_saved(self):
  actor=Actor(ActorKind.MAIN_ADMIN);state=State();message=Message();service=Templates();destinations=Destinations();ack=Ack()
  await open_menu(message,actor,state);token=state.data["mt_token"]
  await callbacks(Query(f"mt:{token}:kind:t",message),actor,ack,state,service,destinations)
  await callbacks(Query(f"mt:{token}:dest:1",message),actor,ack,state,service,destinations)
  page1=message.answers[-1][1];self.assertTrue(any(b.callback_data==f"mt:{token}:dest:2" for row in page1.inline_keyboard for b in row))
  await callbacks(Query(f"mt:{token}:dest:2",message),actor,ack,state,service,destinations)
  page2=message.answers[-1][1];self.assertTrue(any("D9" in b.text for row in page2.inline_keyboard for b in row))
  self.assertTrue(any(b.callback_data==f"mt:{token}:dest:1" for row in page2.inline_keyboard for b in row))
  await callbacks(Query(f"mt:{token}:pick:9",message),actor,ack,state,service,destinations);self.assertEqual(state.data["destination_id"],9)
  await callbacks(Query(f"mt:{token}:edit",message),actor,ack,state,service,destinations)
  await receive_text(Message("CUSTOM {symbol}"),actor,state,service)
  await callbacks(Query(f"mt:{token}:confirm",message),actor,ack,state,service,destinations)
  self.assertEqual(service.rows[DestinationMessageKind.TP_HIT,9],"CUSTOM {symbol}")
  await callbacks(Query(f"mt:{token}:back",message),actor,ack,state,service,destinations);self.assertNotEqual(state.data.get("mt_token"),token)
  new_token=state.data["mt_token"];await callbacks(Query(f"mt:{new_token}:cancel",message),actor,ack,state,service,destinations);self.assertEqual(state.data,{})
 async def test_non_main_admin_is_denied(self):
  state=State();message=Message();await open_menu(message,Actor(ActorKind.UNKNOWN),state)
  self.assertEqual(message.answers[-1][0],texts.UNKNOWN);self.assertEqual(state.data,{})
