import unittest

from billing.webhook import sign, verify_signature

PAYLOAD = b'{"event": "invoice.voided", "id": 7}'


class HiddenVerifySignatureTest(unittest.TestCase):
    def test_prefixed_form_for_another_payload(self):
        self.assertTrue(verify_signature(PAYLOAD, "sha256=" + sign(PAYLOAD)))

    def test_signature_of_another_payload_is_rejected(self):
        self.assertFalse(verify_signature(PAYLOAD, "sha256=" + sign(b"{}")))

    def test_only_the_documented_prefix_is_stripped(self):
        self.assertFalse(verify_signature(PAYLOAD, "sha1=" + sign(PAYLOAD)))

    def test_empty_header_is_rejected(self):
        self.assertFalse(verify_signature(PAYLOAD, ""))
