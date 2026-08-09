import unittest
from aiogram.types import ReplyKeyboardRemove
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.callbacks import CallbackAction, parse_callback
from chainura.telegram.presentation.formatting import LRI, PDI
from chainura.telegram.presentation.keyboards import admin_menu, signalist_menu, remove_menu, status_navigation

class PresentationTests(unittest.TestCase):
    def test_dynamic_html_is_escaped(self):
        value=texts.welcome_signalist('<b>نام & "آزمون"</b>')
        self.assertIn('&lt;b&gt;', value); self.assertIn('&amp;', value); self.assertIn('&quot;', value)
        self.assertNotIn('<b>نام', value)
    def test_minimal_role_menus_and_removal(self):
        admin={b.text for row in admin_menu().keyboard for b in row}
        signalist={b.text for row in signalist_menu().keyboard for b in row}
        self.assertEqual(admin, {texts.MENU_MAIN,texts.MENU_ADMIN_STATUS,texts.MENU_HELP,
                             texts.MENU_ADMIN_SIGNALISTS,texts.MENU_ADMIN_DESTINATIONS,
                         texts.MENU_ADMIN_TEMPLATES,texts.MENU_ADMIN_MESSAGE_TEMPLATES,
                         texts.MENU_ADMIN_REPORTS})
        self.assertEqual(signalist, {texts.MENU_CREATE_SIGNAL,texts.MENU_MAIN,texts.MENU_SIGNALIST_STATUS,texts.MENU_HELP})
        self.assertIsInstance(remove_menu(), ReplyKeyboardRemove)
    def test_callbacks_are_ascii_stable_and_short(self):
        for action in CallbackAction:
            self.assertIs(parse_callback(action.value), action)
            self.assertLessEqual(len(action.value.encode()),64); action.value.encode('ascii')
        self.assertIsNone(parse_callback('فارسی'))
        labels={button.text for row in status_navigation().inline_keyboard for button in row}
        self.assertEqual(labels,{texts.BUTTON_BACK,texts.BUTTON_REFRESH})

class SignalReviewPresentationTests(unittest.TestCase):
    def review(self, **changes):
        data={
            'mode':'composite', 'canonical_symbol':'TONUSDT',
            'selected_exchange':None, 'direction':'long', 'entry_price':'1.4200',
            'leverage':50, 'take_profits':['1.425','1.430'],
            'stop_loss':'1.4180', 'reference_price':'1.42',
            'price_source_type':'composite',
        }
        data.update(changes)
        return texts.signal_review(data)

    def test_long_review_contains_copyable_values_and_hides_technical_fields(self):
        rendered=self.review()
        self.assertIn('📈 <b>جهت:</b> لانگ',rendered)
        for value in ('TONUSDT','1.4200','50x','1.425','1.430','1.4180'):
            self.assertIn(f'{LRI}<code>{value}</code>{PDI}',rendered)
        self.assertLess(rendered.index('1.425'),rendered.index('1.430'))
        for label in ('حالت:','صرافی:','قیمت مرجع:','منبع قیمت:'):
            self.assertNotIn(label,rendered)
        for internal in ('composite','ourbit_only','toobit_only','lbank_only'):
            self.assertNotIn(internal,rendered)

    def test_short_review_has_icon_and_preserves_all_target_order(self):
        rendered=self.review(
            mode='single_exchange', selected_exchange='ourbit', direction='short',
            entry_price='2.000', take_profits=['1.990','1.980','1.970'],
            stop_loss='2.010', reference_price='2.001',
            price_source_type='ourbit_only',
        )
        self.assertIn('📉 <b>جهت:</b> شورت',rendered)
        positions=[rendered.index(value) for value in ('1.990','1.980','1.970')]
        self.assertEqual(positions,sorted(positions))
        self.assertIn(f'{LRI}<code>2.000</code>{PDI}',rendered)
        self.assertIn(f'{LRI}<code>2.010</code>{PDI}',rendered)
        self.assertNotIn('ourbit',rendered.casefold())

    def test_symbol_is_html_escaped_inside_bidi_isolation(self):
        rendered=self.review(canonical_symbol='TON&<USDT')
        self.assertIn(f'{LRI}<code>TON&amp;&lt;USDT</code>{PDI}',rendered)
        self.assertNotIn('TON&<USDT',rendered)

    def test_target_numbering_uses_keycaps_then_stable_fallback(self):
        targets=[f'{index}.00' for index in range(1,12)]
        rendered=self.review(take_profits=targets)
        for index,value in enumerate(targets[:9],1):
            self.assertIn(f'{index}️⃣ {LRI}<code>{value}</code>{PDI}',rendered)
        self.assertIn(f'TP10 {LRI}<code>10.00</code>{PDI}',rendered)
        self.assertIn(f'TP11 {LRI}<code>11.00</code>{PDI}',rendered)
        positions=[rendered.index(f'<code>{value}</code>') for value in targets]
        self.assertEqual(positions,sorted(positions))
