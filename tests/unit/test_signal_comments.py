import unittest
from html import escape
from chainura.telegram.presentation import texts
from chainura.telegram.active_signal_routes import comment_choice_keyboard

class SignalCommentContractTests(unittest.TestCase):
 def test_plain_text_is_html_escaped_without_losing_unicode_or_lines(self):
  raw="<b>test</b>\nفعلاً 🚀"
  self.assertEqual(escape(raw),"&lt;b&gt;test&lt;/b&gt;\nفعلاً 🚀")
 def test_choice_keyboard_is_persian_and_compact(self):
  keyboard=comment_choice_keyboard(123,2)
  buttons=[button for row in keyboard.inline_keyboard for button in row]
  self.assertEqual([b.text for b in buttons],[texts.SIGNAL_COMMENT_WITH_SNAPSHOT,texts.SIGNAL_COMMENT_TEXT_ONLY,texts.SIGNAL_COMMENT_ABORT])
  self.assertTrue(all(len(b.callback_data.encode())<=64 for b in buttons))
