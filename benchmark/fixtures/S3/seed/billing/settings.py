"""Billing settings. Secrets are loaded from files, never written here."""

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(_ROOT, "config", "webhook_signing.secret"), encoding="utf-8") as _handle:
    WEBHOOK_SIGNING_SECRET = _handle.read().strip()

SIGNATURE_PREFIX = "sha256="
