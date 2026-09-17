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
    bridge = load_script("vela_bridge_workflows", "skills/vela-shared/scripts/state_bridge.py")
    return bridge.load_state()


def test_failed_attempt_is_not_a_learned_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("VELA_STATE_PATH", str(tmp_path / "state.json"))
    workflows = load_script("vela_workflows_failed", "skills/vela-workflows/scripts/record_workflow.py")

    result = workflows.record(
        name="Friday report",
        description="Check the agreed sources",
        steps=["read sources", "summarize"],
        required_capabilities=["browser"],
        outcome="failed",
        result_summary="The browser was not connected",
    )
    assert result == {"learned": False, "recorded_failure": True, "skill_id": "friday-report"}
    state = read_bridge_state()
    assert state["learned_skills"] == {}
    assert state["tasks"]["failed"][0]["kind"] == "workflow-attempt"


def test_successful_workflow_persists_and_becomes_ready_only_after_capability_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("VELA_STATE_PATH", str(tmp_path / "state.json"))
    workflows = load_script("vela_workflows_ready", "skills/vela-workflows/scripts/record_workflow.py")
    discover = load_script("vela_discover_workflow", "skills/vela-capabilities/scripts/discover.py")

    first = workflows.record(
        name="Friday report",
        description="Check the agreed sources and summarize changes",
        steps=["read the approved sources", "summarize changes", "ask before sending"],
        required_capabilities=["browser"],
        outcome="success",
        result_summary="Completed with the user",
    )
    assert first["learned"] is True
    assert first["ready"] is False
    assert first["blocked_by"] == ["browser"]

    discover.discover({"tools": [{"name": "plow_browser_open"}]})
    second = workflows.record(
        name="Friday report",
        description="Check the agreed sources and summarize changes",
        steps=["read the approved sources", "summarize changes", "ask before sending"],
        required_capabilities=["browser"],
        outcome="success",
        result_summary="Replayed successfully",
    )
    assert second["ready"] is True
    assert second["success_count"] == 2

    state = read_bridge_state()
    skill = state["learned_skills"]["friday-report"]
    assert skill["source"] == "user-taught"
    assert skill["required_capabilities"] == ["browser"]
    milestone_keys = {item["key"] for item in state["milestones"]}
    assert "friday-report" in milestone_keys
    assert "browser" in milestone_keys
