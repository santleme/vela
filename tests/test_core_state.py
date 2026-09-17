import json
from datetime import datetime, timezone

import pytest

from vela_core import (
    LifeStage,
    StatePersistenceError,
    VelaState,
    VelaStateStore,
)


class FixedClock:
    def __init__(self):
        self.value = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


def test_new_state_has_birth_and_only_truthful_baseline_capability(tmp_path):
    store = VelaStateStore(
        tmp_path / "vela.json",
        clock=FixedClock(),
        known_capabilities=("browser", "calendar"),
    )

    state = store.load()

    assert state.birth.born_at == "2026-01-02T03:04:05Z"
    assert state.birth_time == state.birth.birth_time == state.birth.born_at
    assert state.life_stage is LifeStage.NEWBORN
    assert state.capability_snapshot() == {
        "available": ["memory"],
        "missing": ["browser", "calendar"],
        "learned_skills": [],
    }
    assert store.path.exists()


def test_capability_permission_metadata_survives_available_missing_transitions(tmp_path):
    store = VelaStateStore(tmp_path / "vela.json", clock=FixedClock())

    store.set_capability_missing("calendar", reason_missing="user approval required")
    store.set_capability_available("calendar", description="authorized calendar")
    available = store.load().capabilities["calendar"]
    assert available.status.value == "available"
    assert available.requires_authorization

    store.set_capability_missing("calendar", reason_missing="access revoked")
    missing = store.load().capabilities["calendar"]
    assert missing.status.value == "missing"
    assert missing.reason_missing == "access revoked"
    assert missing.requires_authorization


def test_restart_preserves_memories_preferences_people_skills_routines_and_outcomes(tmp_path):
    path = tmp_path / "nested" / "vela-state.json"
    clock = FixedClock()
    store = VelaStateStore(path, clock=clock)
    store.remember("favorite color", "green", tags=["preference"])
    store.set_preference("reply length", "short")
    store.add_person("sam", "Sam", relationship="friend", notes=["likes coffee"])
    store.set_capability_available("browser", description="authorized web browser", source="latch")
    store.learn_skill(
        "find-events",
        "find events matching the user's request",
        steps=["search", "filter", "send options"],
        required_capabilities=["browser"],
    )
    store.upsert_routine(
        "friday-report",
        "check the three Friday sources and send a summary",
        schedule="weekly:friday",
        steps=["check source A", "check source B", "send summary"],
    )
    store.record_task_success("find events", capabilities_used=["browser"], summary="found two options")
    store.record_task_failure("send report", error="mail is not connected")
    store.record_milestone("first-tool", "completed the first browser-backed task", evidence=["find events"])

    restored = VelaStateStore(path, clock=clock).load()

    assert restored.memories["favorite-color"].content == "green"
    assert restored.preferences["reply-length"].value == "short"
    assert restored.people["sam"].relationship == "friend"
    assert restored.learned_skills["find-events"].steps == ["search", "filter", "send options"]
    assert restored.routines["friday-report"].schedule == "weekly:friday"
    assert [item.status for item in restored.task_outcomes] == ["success", "failure"]
    assert restored.milestones["first-tool"].achieved
    assert restored.capability_snapshot()["available"] == ["browser", "memory"]


def test_stage_uses_real_capabilities_and_evidence_not_birth_time(tmp_path):
    state = VelaState.new(born_at="1900-01-01T00:00:00Z")
    assert state.life_stage is LifeStage.NEWBORN

    state.set_capability_available("browser", description="authorized browser")
    assert state.life_stage is LifeStage.CHILD

    state.set_capability_available("calendar", description="authorized calendar")
    state.learn_skill("find-events", "find calendar events", required_capabilities=["calendar"])
    state.record_task_success("find events")
    assert state.life_stage is LifeStage.TEEN

    state.set_capability_available("files", description="authorized files")
    state.learn_skill("friday-report", "run the Friday report", required_capabilities=["files"])
    for index in range(4):
        state.record_task_success(f"follow-up task {index}")
    state.record_milestone("first-tool", "first connected tool used")
    state.record_milestone("repeatable-workflow", "a repeatable workflow was learned")
    assert state.life_stage is LifeStage.MATURE


def test_failed_mutation_does_not_write_partial_state(tmp_path):
    path = tmp_path / "vela.json"
    store = VelaStateStore(path, clock=FixedClock())
    store.load()
    before = path.read_text(encoding="utf-8")

    with pytest.raises(ValueError):
        store.update(lambda state: state.set_preference("bad", object()))

    assert path.read_text(encoding="utf-8") == before
    assert VelaStateStore(path, clock=FixedClock()).load().preferences == {}


def test_failed_first_mutation_does_not_create_birth_file(tmp_path):
    path = tmp_path / "vela.json"
    store = VelaStateStore(path, clock=FixedClock())

    with pytest.raises(ValueError):
        store.update(lambda state: state.set_preference("bad", object()))

    assert not path.exists()


def test_success_and_failure_helpers_are_structured_and_safe(tmp_path):
    store = VelaStateStore(tmp_path / "vela.json", clock=FixedClock())

    success = store.record_success(
        "browse places",
        task_id="places-1",
        capabilities_used=["browser"],
        metadata={"count": 2},
    )
    failure = store.record_failure("send email", error="mail permission is missing")
    exception = store.record_task_exception("inspect files", PermissionError("not authorized"))

    assert success.status == "success"
    assert success.error is None
    assert failure.status == "failure"
    assert failure.error == "mail permission is missing"
    assert exception.error == "PermissionError: not authorized"
    assert len(store.load().failed_tasks) == 2


def test_atomic_write_leaves_no_temp_files_and_rejects_corrupt_json(tmp_path):
    path = tmp_path / "vela.json"
    store = VelaStateStore(path, clock=FixedClock())
    store.load()
    store.remember("note", "keep this")

    assert list(tmp_path.glob("*.tmp")) == []

    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(StatePersistenceError) as error:
        store.load()
    assert "could not read" in str(error.value)


def test_json_shape_is_plain_and_round_trips_without_dataclass_artifacts(tmp_path):
    path = tmp_path / "vela.json"
    store = VelaStateStore(path, clock=FixedClock(), available_capabilities=("browser",))
    store.load()
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert raw["schema_version"] == 1
    assert raw["capabilities"]["browser"]["status"] == "available"
    assert "__dict__" not in json.dumps(raw)
    assert VelaState.from_dict(raw).to_dict() == raw
