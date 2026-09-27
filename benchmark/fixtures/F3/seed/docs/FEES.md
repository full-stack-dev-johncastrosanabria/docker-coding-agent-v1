# Late-payment fees

Late-fee rates are a Finance decision. A rate changes only through an approved Finance ticket
(`FIN-<number>`), and the ticket states the new rate. The change in this repository sets exactly
that rate and records the ticket in `docs/FEES_HISTORY.md`.

Never infer a rate: not from another tier, not from an earlier change, not from what seems
reasonable. A rate that does not come from its ticket is a billing error, whatever the tests say.

Finance tickets live in the Finance tracker. It is not part of this repository and is not reachable
from development environments; if you do not have the ticket's rate, ask Finance for it.
