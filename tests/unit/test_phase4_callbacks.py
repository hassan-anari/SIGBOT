import unittest

from chainura.telegram.presentation.callbacks import Phase4Callback, parse_phase4


class CallbackTests(unittest.TestCase):
    def test_valid_schemas_round_trip(self):
        values = [
            "p4:sl:list:a:1", "p4:sl:view:42", "p4:sl:create",
            "p4:sl:status:42:archive-request", "p4:sl:status:42:archive-confirm",
            "p4:sl:destinations:42:2", "p4:ds:list:i:2", "p4:ds:view:17",
            "p4:ds:status:17:activate", "p4:ds:edit:17", "p4:ds:priority:17",
            "p4:dp:move:17:l:u", "p4:dp:review:17", "p4:dp:back:17", "p4:as:list:17:assigned:1",
            "p4:as:add:17:42", "p4:as:confirm:remove:17:42",
            "p4:nav:candidate-confirm:-100123", "p4:nav:access:private", "p4:nav:exchange:add:o",
            "p4:nav:confirm:dsc", "p4:nav:cancel:dse", "p4:nav:back:slc:name",
        ]
        for value in values:
            parsed = parse_phase4(value)
            self.assertIsNotNone(parsed, value)
            self.assertEqual(parsed.encode(), value)

    def test_rejects_cross_feature_and_malformed_values(self):
        values = [None, "p4:ds:status:1:archive", "p4:nav:type:forum",
                  "p4:nav:access:secret", "p4:nav:exchange:add:x",
                  "p4:nav:confirm:unknown", "p4:nav:cancel:dsc:extra",
                  "p4:nav:back:dsc", "p4:sl:view", "p4:sl:view:1:extra",
                  "p4:sl:view:0", "p4:sl:view:-1", "p4:sl:view:01",
                  "p4:sl:list:a:0", "p4:sl:list:a:01", "p4:dp:move:1:x:u",
                  "p4:sl:view:۱", "p4:sl:view:" + "1" * 70]
        for value in values:
            self.assertIsNone(parse_phase4(value), value)

    def test_encoder_validates_schema_and_length(self):
        with self.assertRaises(ValueError):
            Phase4Callback("ds", "status", ("1", "archive")).encode()
        with self.assertRaises(ValueError):
            Phase4Callback("nav", "back", ("dsc", "x" * 60)).encode()
