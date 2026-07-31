import unittest
from aiogram.types import ReplyKeyboardRemove
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.callbacks import CallbackAction, parse_callback
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
                                 texts.MENU_ADMIN_SIGNALISTS,texts.MENU_ADMIN_DESTINATIONS})
        self.assertEqual(signalist, {texts.MENU_MAIN,texts.MENU_SIGNALIST_STATUS,texts.MENU_HELP})
        self.assertIsInstance(remove_menu(), ReplyKeyboardRemove)
    def test_callbacks_are_ascii_stable_and_short(self):
        for action in CallbackAction:
            self.assertIs(parse_callback(action.value), action)
            self.assertLessEqual(len(action.value.encode()),64); action.value.encode('ascii')
        self.assertIsNone(parse_callback('فارسی'))
        labels={button.text for row in status_navigation().inline_keyboard for button in row}
        self.assertEqual(labels,{texts.BUTTON_BACK,texts.BUTTON_REFRESH})
