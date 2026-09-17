---
name: vela-shared
description: "Shared persistence and safety rules for Vela's capability, learning, permission and proactive skills."
version: 0.1.0
---

# Vela shared integration contract

Vela's durable state belongs to the Vela core state store. The helper scripts in
this skill expose the only integration boundary the product skills should use:

```text
/var/lib/hermes/skills/vela-shared/scripts/state_bridge.py
```

The bridge first uses `VELA_CORE_CLI` when the deployment provides one, then the
stable importable interface in `vela_core` (`load_state(path)` and
`save_state(path, state)`), and finally the configured `VELA_STATE_PATH` JSON
file when no core implementation is installed yet. A configured core failure is
an error; it is never silently replaced with a second state store.

The state file is private agent state. Do not put Plow tokens, OAuth tokens,
session cookies, raw mail, raw messages or browser credentials in it. Store
short, useful summaries and references only.

The state bridge does not grant access. A capability becomes available only when
the current Hermes/Plow/Latch turn actually exposes the corresponding tool or
relay skill. A request records what Vela would gain and waits for the user to
authorize the integration.

