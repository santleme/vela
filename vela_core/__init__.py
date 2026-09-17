"""Persistent state primitives for Vela.

The package intentionally contains no Hermes, Plow, or transport code.  An
SMS adapter can use :class:`VelaStateStore` to keep Vela's continuity while
leaving integrations responsible for deciding which capabilities are really
available.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .state import (
    BirthMetadata,
    Capability,
    CapabilityStatus,
    LifeStage,
    LearnedSkill,
    Memory,
    Milestone,
    Person,
    Preference,
    Routine,
    StateFormatError,
    StatePersistenceError,
    TaskOutcome,
    VelaState,
    VelaStateStore,
    derive_life_stage,
)


def _bridge_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bridge_defaults(born: str | None = None) -> dict[str, Any]:
    born = born or _bridge_now()
    return {
        "schema_version": 1,
        "birth_time": born,
        "life_stage": "newborn",
        "memories": [],
        "capabilities": {
            "available": {"memory": {"id": "memory", "source": "vela-core", "detected_at": born}},
            "missing": {},
            "last_discovered_at": None,
        },
        "learned_skills": {},
        "capability_requests": [],
        "tasks": {"successful": [], "failed": []},
        "routines": [],
        "important_people": [],
        "preferences": {},
        "milestones": [],
        "proactivity": {"enabled": False, "meaningful_only": True, "sent_events": []},
    }


def _normalise_bridge(value: Mapping[str, Any]) -> dict[str, Any]:
    result = _bridge_defaults(str(value.get("birth_time") or _bridge_now()))
    for key, item in value.items():
        result[key] = item
    caps = result.setdefault("capabilities", {})
    if not isinstance(caps, dict):
        caps = result["capabilities"] = {}
    caps.setdefault("available", {})
    caps.setdefault("missing", {})
    caps.setdefault("last_discovered_at", None)
    result.setdefault("learned_skills", {})
    result.setdefault("capability_requests", [])
    result.setdefault("tasks", {"successful": [], "failed": []})
    result["tasks"].setdefault("successful", [])
    result["tasks"].setdefault("failed", [])
    result.setdefault("routines", [])
    result.setdefault("important_people", [])
    result.setdefault("preferences", {})
    result.setdefault("milestones", [])
    result.setdefault("proactivity", {"enabled": False, "meaningful_only": True, "sent_events": []})
    return result


def _typed_to_bridge(state: VelaState) -> dict[str, Any]:
    """Project a typed core document into the standalone skill contract."""

    bridge = state.extensions.get("bridge", {}) if isinstance(state.extensions, dict) else {}
    result = _bridge_defaults(state.birth_time)
    result.update(
        {
            "schema_version": state.schema_version,
            "birth_time": state.birth_time,
            "life_stage": state.life_stage.value,
            "memories": [item.to_dict() for item in state.memories.values()],
            "learned_skills": {key: item.to_dict() for key, item in state.learned_skills.items()},
            "tasks": {
                "successful": [item.to_dict() for item in state.successful_tasks],
                "failed": [item.to_dict() for item in state.failed_tasks],
            },
            "routines": [item.to_dict() for item in state.routines.values()],
            "important_people": [item.to_dict() for item in state.people.values()],
            "preferences": {key: item.to_dict() for key, item in state.preferences.items()},
            "milestones": [item.to_dict() for item in state.milestones.values()],
            "capability_requests": bridge.get("capability_requests", []),
            "proactivity": bridge.get("proactivity", result["proactivity"]),
        }
    )
    for key, capability in state.capabilities.items():
        bucket = result["capabilities"]["available" if capability.status is CapabilityStatus.AVAILABLE else "missing"]
        bucket[key] = capability.to_dict()
    result["capabilities"]["last_discovered_at"] = bridge.get("last_discovered_at")
    return result


def load_state(path: str | Path) -> dict[str, Any]:
    """Load the JSON contract shared by the Hermes skills.

    The typed store remains available to Python callers, while this small
    adapter deliberately keeps the on-disk integration document compatible
    with skills that run as standalone scripts.
    """

    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _bridge_defaults()
    if not isinstance(raw, Mapping):
        raise ValueError("Vela state must be a JSON object")
    if "birth_time" in raw or "capabilities" in raw and isinstance(raw["capabilities"], Mapping) and "available" in raw["capabilities"]:
        return _normalise_bridge(raw)
    try:
        return _typed_to_bridge(VelaState.from_dict(raw))
    except (StatePersistenceError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid Vela state: {exc}") from exc


def save_state(path: str | Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically persist the skills' JSON state contract."""

    if not isinstance(payload, Mapping):
        raise TypeError("state payload must be an object")
    target = Path(path)
    normalised = _normalise_bridge(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(normalised, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return normalised

__all__ = [
    "BirthMetadata",
    "Capability",
    "CapabilityStatus",
    "LifeStage",
    "LearnedSkill",
    "Memory",
    "Milestone",
    "Person",
    "Preference",
    "Routine",
    "StateFormatError",
    "StatePersistenceError",
    "TaskOutcome",
    "VelaState",
    "VelaStateStore",
    "derive_life_stage",
    "load_state",
    "save_state",
]
