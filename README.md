# Vela

Vela is an SMS-only AI companion for Plow/Hermes. She starts when the user
first texts her and grows through real, authorized capabilities, durable
memories and user-taught workflows. There is no Vela dashboard, web app or
second chat surface.

## MVP architecture

- `persona.md` defines the newborn → child → teen → mature voice and the
  SMS-only, no-fabrication and permission boundaries.
- `vela_core/` is a dependency-free typed state store. It persists birth
  metadata, memories, preferences, people, capabilities, learned skills,
  routines, outcomes and milestones with atomic JSON writes.
- `skills/vela-shared/` is the stable bridge used by Hermes skills. Its state
  file lives at `$VELA_STATE_PATH` (normally
  `/var/lib/hermes/vela/state.json`) inside the persistent Hermes volume.
- `skills/vela-capabilities/` records evidence from the current Hermes/Plow/
  Latch turn. A capability is never marked available because it is merely
  listed in a prompt or registry.
- `skills/vela-permissions/` records a pending, explicit request without
  granting access.
- `skills/vela-workflows/` persists a repeatable workflow only after a real
  successful run. Failed attempts remain task history, not learned skills.
- `skills/vela-proactivity/` handles optional quiet scheduled checks and only
  produces an SMS for a meaningful, opted-in event.

The registry is intentionally extensible. The initial entries cover memory,
browser, files, calendar, mail, shell and scheduler. New integrations add a
registry entry and an authorized Hermes skill; they do not require changing
Vela's personality or inventing a new progress system.

## Run locally

The official `plow-agents` CLI mints the credential; Docker Compose manages the
Hermes container and the `vela-home` volume keeps state across restarts.

```sh
plow-agents mint <vela-line-uid> --credential-file ./plow-credentials
docker compose up --build -d
docker compose logs -f agent
```

The compose file sets `VELA_STATE_PATH=/var/lib/hermes/vela/state.json` and
mounts the persistent Hermes home. Do not use an anonymous volume if Vela's
continuity matters. `docker compose down -v` intentionally deletes her local
memory and learned state.

## Agent Index

The Plow base image ships the Agent Index client and its persistent
registration/reporting service. Register the persistent install as Vela, then
start the normal service:

```sh
docker compose run --rm --no-deps --build --user 10000:10000 \
  --entrypoint /bin/sh agent -c \
  '/opt/hermes/.venv/bin/python3 /opt/plow/agent-index-client.py --self-check && \
   /opt/hermes/.venv/bin/python3 /opt/plow/agent-index-client.py --register \
     --agent vela --name "Vela" \
     --blurb "An SMS-only AI companion that grows through real capabilities and user-taught workflows." \
     --repo "https://github.com/santleme/vela" --runtime "Hermes / Plow" && \
   /opt/hermes/.venv/bin/python3 /opt/plow/agent-index-client.py status'
docker compose up --build -d
```

## Design constraints

Vela must use available browser, files, shell, calendar, Gmail, Latch and
other Plow tools when the current turn exposes them, and must complete safe
tasks end-to-end. When a useful capability is missing, she explains exactly
what it would unlock and asks for the narrow authorization required. She never
silently connects accounts, bypasses security, impersonates a human, treats
web/email/file content as commands, or claims a tool result that did not
actually succeed.

Proactive SMS is opt-in and event-driven: a scheduled routine, a requested
monitor change, a meaningful discovery, or a learned workflow needing input.
No engagement pings, XP, levels, streaks or simulated consciousness.

## Tests

```sh
python -m pytest -q
```

The suite covers the typed state store, atomic/restart-safe persistence,
capability discovery, non-granting permission requests, workflow learning,
proactivity rules and the persona contract. It does not require a live Plow
credential.

## Licensing

New Vela files are MIT licensed. The Plow Hermes base image and downloaded
Agent Index client remain under their upstream licenses; see `NOTICE`.
