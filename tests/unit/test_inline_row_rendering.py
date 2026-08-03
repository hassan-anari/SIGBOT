from __future__ import annotations

import unittest
from PIL import Image, ImageDraw

from chainura.rendering.layouts.models import (BadgeStyle, FontSpec, InlineRowLayer,
    InlineTextItem, Rectangle)
from chainura.rendering.layouts.toobit import (LAYOUT, LEVERAGE_BACKGROUND,
    LONG_BACKGROUND, LONG_TEXT, SHORT_BACKGROUND, SHORT_TEXT)
from chainura.rendering.pillow.errors import RendererLayoutError
from chainura.rendering.pillow.text import DefaultTestFontProvider, draw_inline_row


class InlineRowTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGBA", (600, 120), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.image, "RGBA")
        self.font = DefaultTestFontProvider()

    def test_fixed_content_padding_gaps_and_flexible_remainder(self):
        row = InlineRowLayer("header", Rectangle(10, 10, 410, 90), (
            InlineTextItem("symbol", FontSpec("main", 40, 10), flexible=True),
            InlineTextItem("direction", FontSpec("main", 20, 20),
                           badge=BadgeStyle((1, 2, 3, 255), padding_x=7, padding_y=3)),
            InlineTextItem("leverage", FontSpec("main", 20, 20),
                           badge=BadgeStyle((4, 5, 6, 255), padding_x=9, padding_y=2)),
        ), gap=11)
        result = draw_inline_row(self.draw, row,
            {"symbol":"BTCUSDT", "direction":"Long", "leverage":"10X"}, self.font)
        boxes = [entry[2] for entry in result]
        direction_text = self.draw.textbbox((0, 0), "Long", font=self.font.get("main", 20))
        expected_direction = direction_text[2] - direction_text[0] + 14
        self.assertEqual(boxes[1][2] - boxes[1][0], expected_direction)
        self.assertEqual(boxes[1][0] - boxes[0][2], 11)
        self.assertEqual(boxes[2][0] - boxes[1][2], 11)
        fixed = sum(box[2] - box[0] for box in boxes[1:])
        expected_slot = row.box.width - fixed - row.gap * 2
        self.assertLessEqual(boxes[0][2] - boxes[0][0], expected_slot)

    def test_symbol_shrinks_and_impossible_row_names_identity(self):
        row = LAYOUT.inline_rows[0]
        normal = draw_inline_row(self.draw, row,
            {"symbol":"BTCUSDT", "direction":"Long", "leverage":"10X"}, self.font)
        long = draw_inline_row(self.draw, row,
            {"symbol":"VERYLONGCANONICALTOKENUSDT", "direction":"Long", "leverage":"125X"}, self.font)
        self.assertLess(long[0][1], normal[0][1])
        self.assertTrue(all(left[2][2] + row.gap == right[2][0]
                            for left, right in zip(long, long[1:])))
        impossible = InlineRowLayer("tiny_identity", Rectangle(0, 0, 10, 10), (
            InlineTextItem("symbol", FontSpec("main", 10, 10), flexible=True),
            InlineTextItem("direction", FontSpec("main", 10, 10)),
        ), gap=20)
        with self.assertRaisesRegex(RendererLayoutError, "tiny_identity"):
            draw_inline_row(self.draw, impossible,
                            {"symbol":"LONG", "direction":"Long"}, self.font)

    def test_toobit_dynamic_badge_palette_and_gray_leverage(self):
        row = LAYOUT.inline_rows[0]
        direction, leverage = row.items[1:]
        self.assertEqual(direction.fill, LONG_TEXT)
        self.assertEqual(direction.fill_by_value, (("Short", SHORT_TEXT),))
        self.assertEqual(direction.badge.fill, LONG_BACKGROUND)
        self.assertEqual(direction.badge.fill_by_value, (("Short", SHORT_BACKGROUND),))
        self.assertEqual(leverage.badge.fill, LEVERAGE_BACKGROUND)

    def test_model_is_tuple_based_and_frozen(self):
        row = LAYOUT.inline_rows[0]
        self.assertIsInstance(row.items, tuple)
        with self.assertRaises((AttributeError, TypeError)):
            row.gap = 2


if __name__ == "__main__":
    unittest.main()
