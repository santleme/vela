# Minimal Plow Hermes variant for Vela.
# The base is immutable and already contains the runtime, Plow integration,
# persistent Hermes home, s6-overlay, Agent Index reporter, and base persona.
FROM public.ecr.aws/e1h7x4a2/plow-cloud-agents:base-67021a7029e33e80bcb27899be6515a5a0e9b37b@sha256:0c3892e93c1a001c61fb7106396e0a4b7e0219008184fd90719caa84a3390ff0

LABEL org.opencontainers.image.title="Vela"
LABEL org.opencontainers.image.description="Vela SMS-only AI companion for Plow"
LABEL org.opencontainers.image.source="https://github.com/santleme/vela"
LABEL org.opencontainers.image.licenses="MIT"

# Vela's product code is immutable in the image. Only the JSON state under the
# Hermes home is writable by the running agent, so a prompt cannot rewrite the
# code used by a future scheduled wake.
ENV VELA_CORE_ROOT=/opt/vela \
    VELA_STATE_PATH=/var/lib/hermes/vela/state.json

COPY vela_core/ /opt/vela/vela_core/
COPY skills/ /opt/hermes/skills/
RUN find /opt/vela -type d -exec chmod 0755 {} + \
 && find /opt/vela -type f -exec chmod 0644 {} + \
 && find /opt/hermes/skills -mindepth 1 -type d -exec chmod 0755 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f ! -perm -u+x -exec chmod 0644 {} + \
 && find /opt/hermes/skills -mindepth 1 -type f -perm -u+x -exec chmod 0755 {} + \
 && install -d -o 10000 -g 10000 -m 0700 /var/lib/hermes/vela

# plow-init composes the final SOUL.md from the base persona and this file on
# every boot. Never copy an identity into /var/lib/hermes/SOUL.md.
COPY --chown=0:0 persona.md /opt/hermes/plow-seed/persona.md
RUN chmod 0644 /opt/hermes/plow-seed/persona.md

COPY LICENSE NOTICE /usr/share/doc/vela/
COPY image/s6-overlay/ /etc/s6-overlay/
RUN chmod 0755 /etc/s6-overlay/s6-rc.d/vela-sms-mode/up \
 && chmod 0755 /etc/s6-overlay/scripts/vela-sms-mode.sh

VOLUME ["/var/lib/hermes"]
