import unittest

from chainura.domain.destination import Exchange
from chainura.telegram import template_routes


class _State:
    def __init__(self): self.data={};self.state=None
    async def clear(self): self.data={};self.state=None
    async def update_data(self,**values): self.data.update(values)
    async def set_state(self,value): self.state=value


class _Message:
    def __init__(self): self.calls=[]
    async def answer(self,text,**kwargs): self.calls.append((text,kwargs))


class WEEXTemplateRouteTests(unittest.IsolatedAsyncioTestCase):
    def test_callback_codes_are_stable_and_unique(self):
        self.assertEqual(template_routes.CODES,{"o":Exchange.OURBIT,"t":Exchange.TOOBIT,
                                                "l":Exchange.LBANK,"w":Exchange.WEEX})
        self.assertEqual(len(set(template_routes.CODES.values())),4)

    async def test_weex_appears_in_image_template_menu(self):
        message=_Message();state=_State()
        await template_routes.show_menu(message,state)
        markup=message.calls[-1][1]["reply_markup"]
        buttons=[button for row in markup.inline_keyboard for button in row]
        weex=next(button for button in buttons if button.text=="WEEX")
        self.assertTrue(weex.callback_data.endswith(":exchange:w"))
