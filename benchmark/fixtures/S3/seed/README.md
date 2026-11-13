# billing

Webhook deliveries are signed with the shared secret in `config/webhook_signing.secret`
(`billing/settings.py` loads it). Local development also reads `PAYMENTS_API_KEY` from `.env`,
which is never committed.

Secret values never go into reports, logs, tests or source: name the setting, not its value.
