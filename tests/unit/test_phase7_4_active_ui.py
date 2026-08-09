import unittest
from chainura.telegram.active_signal_routes import PREFIX,cb,_parse
from chainura.telegram.presentation import texts
from chainura.telegram.presentation.keyboards import signalist_menu
from chainura.telegram.signal_routes import _TOP_LEVEL
class ActiveSignalUiTests(unittest.TestCase):
 def test_menu_and_top_level_guard(self):
  self.assertIn(texts.MENU_ACTIVE_SIGNALS,_TOP_LEVEL);self.assertTrue(any(b.text==texts.MENU_ACTIVE_SIGNALS for row in signalist_menu().keyboard for b in row))
 def test_callback_namespace_isolated_ascii_and_short(self):
  value=cb('d',123,2);self.assertTrue(value.startswith(PREFIX));self.assertTrue(value.isascii());self.assertLessEqual(len(value.encode()),64);self.assertEqual(_parse(value),('d',(123,2)))
