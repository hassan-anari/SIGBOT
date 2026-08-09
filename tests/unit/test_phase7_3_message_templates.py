import unittest

from chainura.application.message_templates import (BUILTIN_TEMPLATES, MessageTemplateValidationError,
    SafeDynamicValue, render_template, validate_template)
from chainura.domain.message_template import DestinationMessageKind as Kind


class MessageTemplateRenderingTests(unittest.TestCase):
    def test_every_builtin_validates_and_renders(self):
        sample={name:"value" for names in __import__('chainura.application.message_templates',fromlist=['PLACEHOLDERS']).PLACEHOLDERS.values() for name in names}
        sample["private_tag"]=""
        for kind,template in BUILTIN_TEMPLATES.items():
            with self.subTest(kind=kind): self.assertTrue(render_template(kind,template,sample))

    def test_rejects_unsafe_placeholder_forms(self):
        for value in ("{unknown}","{symbol.__class__}","{symbol[0]}","{symbol!r}","{roi:.2f}","{symbol"):
            with self.subTest(value=value),self.assertRaises(MessageTemplateValidationError):
                validate_template(Kind.TP_HIT,value)

    def test_dynamic_values_are_escaped_but_valid_markup_survives(self):
        rendered=render_template(Kind.TP_HIT,"<b>{symbol}</b>",{"symbol":"<i>bad</i>"})
        self.assertEqual(rendered,"<b>&lt;i&gt;bad&lt;/i&gt;</b>")

    def test_application_safe_html_is_preserved(self):
        rendered=render_template(Kind.TP_HIT,"<b>{symbol}</b>",{"symbol":SafeDynamicValue("⁦<code>BTCUSDT</code>⁩")})
        self.assertIn("<code>BTCUSDT</code>",rendered)

    def test_markup_and_limits(self):
        validate_template(Kind.TP_HIT,"<b>ok</b>")
        for bad in ("<b>bad", "<a href='x'>bad</a>"):
            with self.assertRaises(MessageTemplateValidationError):validate_template(Kind.TP_HIT,bad)
        self.assertEqual(len(render_template(Kind.TP_HIT,"x"*1024,{})),1024)
        with self.assertRaises(MessageTemplateValidationError):render_template(Kind.TP_HIT,"x"*1025,{})
        self.assertEqual(len(render_template(Kind.SL_HIT,"x"*4096,{})),4096)
        with self.assertRaises(MessageTemplateValidationError):render_template(Kind.SL_HIT,"x"*4097,{})

    def test_optional_private_tag_has_clean_whitespace(self):
        text=render_template(Kind.SIGNAL_CANCELLED,"hello\n\n{private_tag}",{"private_tag":""})
        self.assertEqual(text,"hello")
