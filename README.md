# Vela

Vela is an early, intentionally minimal Plow Hermes agent. Its product concept,
persona details, skills, branding, and description are deliberately easy to
replace later; the Plow runtime and Agent Index integration are the stable
infrastructure layer.

## Current identity

- Display name: Vela
- Initial Agent Index ID: `vela`
- Runtime: Hermes / Plow
- License for agent-specific files: MIT

The identity is only published after registration with the official Agent Index
client. If `vela` is unavailable, keep the display name Vela and use the
shortest available fallback ID chosen during registration.

## Local build and run

The official `plow-agents` CLI writes a mode-600 `plow-credentials` file for
this agent. It is ignored by both Git and Docker. Use a separate Plow line and
credential for Vela; never reuse Mako's credential.

```sh
plow-agents mint <vela-line-uid> --credential-file ./plow-credentials
docker compose up --build -d
docker compose logs -f agent
```

The named `vela-home` volume persists Hermes state and
`/var/lib/hermes/.agent-index.json`, which preserves this install's Agent Index
identity across container recreation. Do not use a fresh anonymous volume for a
recreated install.

## Agent Index registration and checks

The image fetches exactly the pinned client in `vendor/client.pin` and verifies
its SHA-256 during the image build. From this directory, with the credential
loaded only into the current shell:

```sh
curl -fsSLO https://raw.githubusercontent.com/plow-pbc/agent-index-client/f900ff144076f0a766584b6ec4d0993600779b16/standalone/agent_index_client.py
chmod +x agent_index_client.py
./agent_index_client.py --self-check
set -a; . ./plow-credentials; set +a
./agent_index_client.py --register --agent vela --name "Vela" \
  --blurb "A lightweight Plow agent for recurring real-world workflows through Plow. Initial capabilities are intentionally minimal while its product direction is being finalized." \
  --runtime "Hermes / Plow"
./agent_index_client.py status
./agent_index_client.py --agent vela --dry-run
```

The runtime reporter is the official s6 longrun under
`image/s6-overlay/s6-rc.d/agent-index`. It registers only when the client says
the persistent install state is absent, gives the Plow bearer only to that
registration exchange, and reports every five minutes without the bearer.

## Later evolution

Add product-specific skills under this repository only when the product is
chosen. Keep the base image, Agent Index client pin, persistent Hermes home,
and reporter wiring intact.

## Licensing

New Vela files are MIT licensed. The Plow Hermes base image and the downloaded
Agent Index client remain under their upstream licenses; see NOTICE.

