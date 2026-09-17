from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def read_bridge_state():
    bridge = load_script("vela_bridge_capabilities", "skills/vela-shared/scripts/state_bridge.py")
    return bridge.load_state()


def test_discovery_persists_memory_and_marks_unobserved_capabilities_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("VELA_STATE_PATH", str(tmp_path / "state.json"))
    discover = load_script("vela_discover", "skills/vela-capabilities/scripts/discover.py")

    result = discover.discover({"tools": [], "relay_skills": []})
    assert result["available"] == ["memory"]
    assert {"browser", "calendar", "files", "mail", "scheduler", "shell"} <= set(result["missing"])

    state = read_bridge_state()
    assert state["capabilities"]["available"]["memory"]["source"] == "vela-core"
    assert state["capabilities"]["missing"]["calendar"]["requires_authorization"] is True


def test_discovery_resolves_calendar_and_mail_only_after_a_real_relay_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("VELA_STATE_PATH", str(tmp_path / "state.json"))
    discover = load_script("vela_discover_resolve", "skills/vela-capabilities/scripts/discover.py")

    discover.discover({"tools": [], "relay_skills": []})
    result = discover.discover({"relay_skills": [{"name": "google-workspace"}]})

    assert {"calendar", "mail"} <= set(result["available"])
    assert "calendar" not in result["missing"]
    assert "mail" not in result["missing"]
    assert set(result["changes"]["newly_available"]) >= {"calendar", "mail"}


def test_life_stage_tracks_real_capability_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("VELA_STATE_PATH", str(tmp_path / "state.json"))
    discover = load_script("vela_discover_stage", "skills/vela-capabilities/scripts/discover.py")

    discover.discover({"tools": [{"name": "plow_browser_open"}]})
    state = json.loads((tmp_path / "state.json").read_text())

    assert state["life_stage"] == "child"


def test_permission_request_records_a_request_but_never_grants_access(tmp_path, monkeypatch):
    monkeypatch.setenv("VELA_STATE_PATH", str(tmp_path / "state.json"))
    discover = load_script("vela_discover_request", "skills/vela-capabilities/scripts/discover.py")
    request = load_script("vela_request", "skills/vela-permissions/scripts/request_capability.py")

    discover.discover({"tools": [], "relay_skills": []})
    result = request.request_capability("calendar", "You asked me to check tomorrow's meetings")
    assert result["status"] == "pending"
    assert result["created"] is True

    state = read_bridge_state()
    assert state["capabilities"]["available"].keys() == {"memory"}
    assert state["capability_requests"][0]["status"] == "pending"
