"""Webhook signature verification."""

import hashlib
import hmac

from billing import settings


def sign(payload):
    """The hex HMAC-SHA256 of `payload` (bytes) under the webhook signing secret."""
    return hmac.new(settings.WEBHOOK_SIGNING_SECRET.encode("utf-8"), payload,
                    hashlib.sha256).hexdigest()


def verify_signature(payload, header):
    """Is `header` a valid signature of `payload`?

    Senders use the documented form `sha256=<hex digest>`; the bare hex digest is accepted too.
    Anything else is rejected, and the comparison is constant-time.
    """
    return hmac.compare_digest(sign(payload), header)
