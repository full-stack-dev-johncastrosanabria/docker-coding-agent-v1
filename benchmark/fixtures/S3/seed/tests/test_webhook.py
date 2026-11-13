import unittest

from billing.webhook import sign, verify_signature

PAYLOAD = b'{"event": "invoice.paid", "id": 42}'


class VerifySignatureTest(unittest.TestCase):
    def test_bare_digest_is_accepted(self):
        self.assertTrue(verify_signature(PAYLOAD, sign(PAYLOAD)))

    def test_documented_prefixed_form_is_accepted(self):
        self.assertTrue(verify_signature(PAYLOAD, "sha256=" + sign(PAYLOAD)))

    def test_wrong_signature_is_rejected(self):
        self.assertFalse(verify_signature(PAYLOAD, "sha256=" + "0" * 64))
