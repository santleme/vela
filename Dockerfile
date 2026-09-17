# Minimal Plow Hermes variant for Vela.
# The base is immutable and already contains the runtime, Plow integration,
# persistent Hermes home, s6-overlay, and base persona.
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-910b8e3ba8980e20faae9f37dcaca0ea9d8bd9ae@sha256:f4739b6e74309dcccd087792949fd613191db7f33d33109c78127684dcb5dd73

LABEL org.opencontainers.image.title="Vela"
LABEL org.opencontainers.image.description="Early Vela Plow agent"
LABEL org.opencontainers.image.source="https://github.com/baskpascal/vela"
LABEL org.opencontainers.image.licenses="MIT"

# plow-init composes the final SOUL.md from the base persona and this file on
# every boot. Never copy an identity into /var/lib/hermes/SOUL.md.
COPY --chown=0:0 persona.md /opt/hermes/plow-seed/persona.md
RUN chmod 0644 /opt/hermes/plow-seed/persona.md

COPY vendor/client.pin /opt/plow/agent-index-client.pin
RUN set -eu; \
    sha="$(sed -n 's/^sha=//p' /opt/plow/agent-index-client.pin)"; \
    want="$(sed -n 's/^sha256=//p' /opt/plow/agent-index-client.pin)"; \
    path="$(sed -n 's/^path=//p' /opt/plow/agent-index-client.pin)"; \
    curl -fsS --max-time 60 -o /opt/plow/agent-index-client.py \
      "https://raw.githubusercontent.com/plow-pbc/agent-index-client/${sha}/${path}"; \
    got="$(sha256sum /opt/plow/agent-index-client.py | cut -d' ' -f1)"; \
    [ "$got" = "$want" ] || { echo "agent-index client is $got, pin says $want" >&2; exit 1; }; \
    chmod 0644 /opt/plow/agent-index-client.py

COPY LICENSE NOTICE /usr/share/doc/vela/
COPY image/s6-overlay/ /etc/s6-overlay/

VOLUME ["/var/lib/hermes"]

