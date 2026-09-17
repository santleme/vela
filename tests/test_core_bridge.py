from __future__ import annotations

import json

from vela_core import load_state, save_state


def test_bridge_bootstraps_truthful_state_and_preserves_skill_fields(tmp_path):
    path = tmp_path / "vela" / "state.json"

    state = load_state(path)
    assert state["capabilities"]["available"]["memory"]["source"] == "vela-core"
    assert state["birth_time"]

    state["capabilities"]["missing"]["calendar"] = {
        "id": "calendar",
        "requires_authorization": True,
        "reason": "not connected",
    }
    state["capability_requests"].append(
        {"request_id": "capability:calendar", "capability": "calendar", "status": "pending"}
    )
    state["proactivity"]["enabled"] = True
    save_state(path, state)

    restored = load_state(path)
    assert restored["capabilities"]["missing"]["calendar"]["requires_authorization"] is True
    assert restored["capability_requests"][0]["status"] == "pending"
    assert restored["proactivity"]["enabled"] is True
    assert json.loads(path.read_text(encoding="utf-8"))["birth_time"] == state["birth_time"]
