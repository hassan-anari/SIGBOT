import unittest
from chainura.telegram.presentation.callbacks import Phase4Callback,parse_phase4
class CallbackTests(unittest.TestCase):
    def test_examples_round_trip(self):
        values=["p4:sl:list:a:1","p4:sl:view:42","p4:sl:status:42:archive:confirm","p4:ds:list:i:2","p4:ds:view:17","p4:ds:status:17:activate","p4:dp:move:17:l:u","p4:as:list:17:assigned:1","p4:as:add:17:42","p4:as:confirm:remove:17:42"]
        for value in values: self.assertEqual(parse_phase4(value).encode(),value)
    def test_rejects_malformed(self):
        for value in (None,"p4:sl:view:0","p4:sl:list:x:1","p4:sl:list:a:01","p4:dp:move:1:x:u","p4:sl:view:۱","p4:sl:view:"+"1"*70): self.assertIsNone(parse_phase4(value))
    def test_encoder_limit(self):
        with self.assertRaises(ValueError): Phase4Callback("nav","back",("x"*60,)).encode()
