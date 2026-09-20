# Recorded negative states for the G7 detector

Recorded `sbx settings get --json` documents for the two states the V1 SSH detector must
refuse. G7 feeds them to the same detector it runs against the live state, so the refusals are
proven **without changing any global setting** (tasks.md T010).

The two are refused for different reasons: forwarding enabled is **known unsafe**, while a
configured fixed agent socket is **rejected by strict V1 policy** unless it is separately proven
safe on the pinned version.

Their shape is copied from the live documents this host emits; only the values differ.

| File | State | Detector must |
|---|---|---|
| `forwarding-enabled.json` | `ssh.agentForwardingEnabled: true` — known unsafe | refuse |
| `fixed-socket.json` | `ssh.agentSocketPath` set to a fixed host socket — refused by strict V1 policy, not proven to forward | refuse |
