from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_proactive_gate_is_quiet_until_explicitly_enabled_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setenv("VELA_STATE_PATH", str(tmp_path / "state.json"))
    proactive = load_script("vela_proactive", "skills/vela-proactive/scripts/proactive.py")

    event = {"event_id": "routine:friday", "kind": "routine", "meaningful": True, "message": "hey\ni figured out your Friday thing"}
    assert proactive.evaluate(event)["action"] == "quiet"

    assert proactive.enable(True)["enabled"] is True
    ready = proactive.evaluate(event)
    assert ready["action"] == "send"
    assert ready["message"].startswith("hey")

    proactive.mark_sent(ready["fingerprint"], event["event_id"])
    duplicate = proactive.evaluate(event)
    assert duplicate["action"] == "quiet"
    assert duplicate["reason"] == "event-already-sent"

    noisy = proactive.evaluate({"meaningful": False, "message": "nothing changed"})
    assert noisy["action"] == "quiet"
    assert noisy["reason"] == "event-not-marked-meaningful"


def test_cron_registration_is_opt_in_idempotent_and_requires_home_channel(tmp_path, monkeypatch):
    register = load_script("vela_register", "skills/vela-proactive/scripts/register_job.py")
    monkeypatch.setattr(register, "HERMES", "hermes")
    monkeypatch.setattr(register.shutil, "which", lambda _: "C:/fake/hermes")
    jobs_path = tmp_path / "jobs.json"
    env = {"PLOW_HOME_CHANNEL": "cht_owner"}
    calls = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def runner(argv):
        calls.append(argv)
        return Completed()

    monkeypatch.setenv("VELA_STATE_PATH", str(tmp_path / "state.json"))
    with pytest.raises(SystemExit, match="opt-in"):
        register.main([], runner=runner, jobs_path=jobs_path, env=env)
    register.main(["--enable", "--schedule", "0 9 * * 5"], runner=runner, jobs_path=jobs_path, env=env)
    assert len(calls) == 1
    assert calls[0][-2:] == ["--deliver", "plow_chat:cht_owner"]
    assert "NO_REPLY" in calls[0][4]

    jobs_path.write_text(json.dumps({"jobs": [{"name": "vela-proactive", "enabled": True, "paused_at": None}]}))
    register.main(["--enable"], runner=runner, jobs_path=jobs_path, env=env)
    assert len(calls) == 1

    with pytest.raises(SystemExit, match="PLOW_HOME_CHANNEL"):
        register.create_argv("0 9 * * 5", {})
