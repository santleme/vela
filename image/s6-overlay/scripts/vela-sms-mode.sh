#!/bin/sh
set -eu

sms_only=true
if [ -f /run/s6/container_environment/VELA_SMS_ONLY ]; then
  sms_only=$(cat /run/s6/container_environment/VELA_SMS_ONLY)
fi

if [ "$sms_only" != "true" ]; then
  /opt/hermes/.venv/bin/python3 - <<'PY'
from pathlib import Path
import os
import yaml

config_path = Path("/var/lib/hermes/config.yaml")
if config_path.exists():
    config = yaml.safe_load(config_path.read_text()) or {}
    server = config.get("mcp_servers", {}).get("plow")
    if isinstance(server, dict):
        server["enabled"] = True
        temporary = config_path.with_suffix(".yaml.vela-tmp")
        temporary.write_text(yaml.safe_dump(config, sort_keys=False))
        os.chmod(temporary, 0o640)
        os.replace(temporary, config_path)
PY
  exit 0
fi

# plow-init may discover a parked Mac relay and export PLOW_MCP_URL. Vela's
# current role does not need it, and the official plugin otherwise probes it
# during startup. Remove only that environment entry and disable its config;
# the gateway and the Plow Chat platform remain enabled.
rm -f /run/s6/container_environment/PLOW_MCP_URL

# Repair a state file created by a root registration command so the reporter's
# hermes user can update it. Do not touch Vela's JSON state or the whole volume.
if [ -e /var/lib/hermes/.agent-index-state.json ]; then
  chown hermes:hermes /var/lib/hermes/.agent-index-state.json
fi

/opt/hermes/.venv/bin/python3 - <<'PY'
import os
from pathlib import Path

import yaml

config_path = Path("/var/lib/hermes/config.yaml")
config = yaml.safe_load(config_path.read_text()) or {}
server = config.get("mcp_servers", {}).get("plow")
if isinstance(server, dict):
    server["enabled"] = False
    temporary = config_path.with_suffix(".yaml.vela-tmp")
    temporary.write_text(yaml.safe_dump(config, sort_keys=False))
    os.chmod(temporary, 0o640)
    os.replace(temporary, config_path)
PY
