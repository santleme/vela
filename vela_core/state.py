"""Small, dependency-free, restart-safe state store for Vela.

This module deliberately models facts about Vela rather than an artificial
progress meter.  Capabilities are only marked available by an integration
that has actually obtained access, and learned skills contain reusable
instructions/evidence instead of XP.

The store uses a single JSON document and atomic ``os.replace`` writes.  The
document is self-contained so it can be copied, inspected, and migrated by a
future Hermes/Plow adapter without a database dependency.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Union


CURRENT_SCHEMA_VERSION = 1
DEFAULT_STATE_FILENAME = "vela-state.json"
class StatePersistenceError(RuntimeError):
    """Raised when the state document cannot be safely read or written."""


class StateFormatError(StatePersistenceError):
    """Raised when a state document is valid JSON but has an invalid shape."""


class LifeStage(str, Enum):
    """A narrative stage derived from real evidence in :class:`VelaState`."""

    NEWBORN = "newborn"
    CHILD = "child"
    TEEN = "teen"
    MATURE = "mature"


class CapabilityStatus(str, Enum):
    """The two states relevant to the capability registry."""

    AVAILABLE = "available"
    MISSING = "missing"


Clock = Callable[[], Union[datetime, str]]


def utc_now() -> str:
    """Return a compact UTC timestamp suitable for persisted JSON."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp(clock: Optional[Clock] = None) -> str:
    value = clock() if clock is not None else utc_now()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError("clock must return a non-empty ISO timestamp or datetime")


def _key(value: str, label: str = "key") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    # Keys are identifiers, not user-facing copy.  Stable normalization keeps
    # lookups predictable across SMS messages and process restarts.
    normalized = re.sub(r"[^a-z0-9._:-]+", "-", value.strip().casefold()).strip("-")
    if not normalized:
        raise ValueError(f"{label} must contain at least one identifier character")
    return normalized


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: Optional[str], label: str) -> Optional[str]:
    if value is None:
        return None
    return _text(value, label)


def _json_value(value: Any, label: str) -> Any:
    """Validate and detach a value that will be embedded in JSON."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON-serializable") from exc


def _string_list(values: Optional[Iterable[str]], label: str) -> List[str]:
    if values is None:
        return []
    result = []
    for value in values:
        result.append(_text(value, label))
    return result


def _dict(value: Any, label: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return copy.deepcopy(dict(value))


@dataclass
class BirthMetadata:
    """Metadata for the first persisted appearance of Vela."""

    born_at: str
    cause: str = "first_message"
    first_message_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.born_at = _text(self.born_at, "born_at")
        self.cause = _text(self.cause, "cause")
        self.first_message_id = _optional_text(self.first_message_id, "first_message_id")
        self.metadata = _dict(self.metadata, "metadata")

    @property
    def birth_time(self) -> str:
        """Compatibility/readability alias for the persisted birth timestamp."""

        return self.born_at

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BirthMetadata":
        if not isinstance(value, Mapping):
            raise StateFormatError("birth must be an object")
        return cls(
            born_at=value.get("born_at") or value.get("birth_time") or "",
            cause=value.get("cause", "first_message"),
            first_message_id=value.get("first_message_id"),
            metadata=value.get("metadata", {}),
        )


@dataclass
class Memory:
    key: str
    content: str
    created_at: str
    updated_at: str
    source: str = "conversation"
    confidence: float = 1.0
    tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.key = _key(self.key, "memory key")
        self.content = _text(self.content, "memory content")
        self.source = _text(self.source, "memory source")
        if not isinstance(self.confidence, (int, float)) or not 0 <= float(self.confidence) <= 1:
            raise ValueError("memory confidence must be between 0 and 1")
        self.confidence = float(self.confidence)
        self.tags = _string_list(self.tags, "memory tag")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Memory":
        return cls(
            key=value.get("key", ""),
            content=value.get("content", ""),
            created_at=value.get("created_at", ""),
            updated_at=value.get("updated_at", ""),
            source=value.get("source", "conversation"),
            confidence=value.get("confidence", 1.0),
            tags=value.get("tags", []),
        )


@dataclass
class Preference:
    key: str
    value: Any
    updated_at: str
    source: str = "conversation"

    def __post_init__(self) -> None:
        self.key = _key(self.key, "preference key")
        self.value = _json_value(self.value, "preference value")
        self.updated_at = _text(self.updated_at, "preference updated_at")
        self.source = _text(self.source, "preference source")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Preference":
        return cls(
            key=value.get("key", ""),
            value=value.get("value"),
            updated_at=value.get("updated_at", ""),
            source=value.get("source", "conversation"),
        )


@dataclass
class Person:
    key: str
    name: str
    relationship: Optional[str]
    notes: List[str]
    updated_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.key = _key(self.key, "person key")
        self.name = _text(self.name, "person name")
        self.relationship = _optional_text(self.relationship, "relationship")
        self.notes = _string_list(self.notes, "person note")
        self.updated_at = _text(self.updated_at, "person updated_at")
        self.metadata = _dict(self.metadata, "person metadata")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Person":
        return cls(
            key=value.get("key", ""),
            name=value.get("name", ""),
            relationship=value.get("relationship"),
            notes=value.get("notes", []),
            updated_at=value.get("updated_at", ""),
            metadata=value.get("metadata", {}),
        )


@dataclass
class Capability:
    key: str
    status: CapabilityStatus
    description: str
    reason_missing: Optional[str]
    requires_authorization: bool
    source: Optional[str]
    updated_at: str
    granted_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.key = _key(self.key, "capability key")
        self.status = CapabilityStatus(self.status)
        self.description = _text(self.description, "capability description")
        self.reason_missing = _optional_text(self.reason_missing, "reason_missing")
        if not isinstance(self.requires_authorization, bool):
            raise ValueError("requires_authorization must be a boolean")
        self.source = _optional_text(self.source, "capability source")
        self.updated_at = _text(self.updated_at, "capability updated_at")
        self.granted_at = _optional_text(self.granted_at, "granted_at")
        self.metadata = _dict(self.metadata, "capability metadata")
        if self.status is CapabilityStatus.AVAILABLE:
            self.reason_missing = None
        elif self.granted_at is not None:
            # A historical grant timestamp is useful, but an unavailable
            # capability must not be presented as currently granted.
            self.granted_at = None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Capability":
        return cls(
            key=value.get("key", ""),
            status=value.get("status", CapabilityStatus.MISSING.value),
            description=value.get("description") or value.get("name") or "",
            reason_missing=value.get("reason_missing"),
            requires_authorization=value.get("requires_authorization", False),
            source=value.get("source"),
            updated_at=value.get("updated_at", ""),
            granted_at=value.get("granted_at"),
            metadata=value.get("metadata", {}),
        )


@dataclass
class LearnedSkill:
    key: str
    description: str
    steps: List[str]
    required_capabilities: List[str]
    created_at: str
    updated_at: str
    successful_uses: int = 0
    last_used_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.key = _key(self.key, "skill key")
        self.description = _text(self.description, "skill description")
        self.steps = _string_list(self.steps, "skill step")
        self.required_capabilities = [_key(item, "required capability") for item in self.required_capabilities]
        self.created_at = _text(self.created_at, "skill created_at")
        self.updated_at = _text(self.updated_at, "skill updated_at")
        if not isinstance(self.successful_uses, int) or self.successful_uses < 0:
            raise ValueError("successful_uses must be a non-negative integer")
        self.last_used_at = _optional_text(self.last_used_at, "last_used_at")
        self.metadata = _dict(self.metadata, "skill metadata")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LearnedSkill":
        return cls(
            key=value.get("key", ""),
            description=value.get("description", ""),
            steps=value.get("steps", []),
            required_capabilities=value.get("required_capabilities", []),
            created_at=value.get("created_at", ""),
            updated_at=value.get("updated_at", ""),
            successful_uses=value.get("successful_uses", 0),
            last_used_at=value.get("last_used_at"),
            metadata=value.get("metadata", {}),
        )


@dataclass
class TaskOutcome:
    task_id: str
    task: str
    status: str
    recorded_at: str
    summary: Optional[str] = None
    error: Optional[str] = None
    capabilities_used: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.task_id = _key(self.task_id, "task_id")
        self.task = _text(self.task, "task")
        if self.status not in {"success", "failure"}:
            raise ValueError("task status must be 'success' or 'failure'")
        self.recorded_at = _text(self.recorded_at, "recorded_at")
        self.summary = _optional_text(self.summary, "summary")
        self.error = _optional_text(self.error, "error")
        self.capabilities_used = [_key(item, "capability used") for item in self.capabilities_used]
        self.metadata = _dict(self.metadata, "task metadata")
        if self.status == "success":
            self.error = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskOutcome":
        return cls(
            task_id=value.get("task_id", ""),
            task=value.get("task", ""),
            status=value.get("status", "failure"),
            recorded_at=value.get("recorded_at", ""),
            summary=value.get("summary"),
            error=value.get("error"),
            capabilities_used=value.get("capabilities_used", []),
            metadata=value.get("metadata", {}),
        )


@dataclass
class Routine:
    key: str
    description: str
    schedule: Optional[str]
    steps: List[str]
    enabled: bool
    created_at: str
    updated_at: str
    last_run_at: Optional[str] = None
    last_run_status: Optional[str] = None
    last_run_summary: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.key = _key(self.key, "routine key")
        self.description = _text(self.description, "routine description")
        self.schedule = _optional_text(self.schedule, "schedule")
        self.steps = _string_list(self.steps, "routine step")
        if not isinstance(self.enabled, bool):
            raise ValueError("routine enabled must be a boolean")
        self.created_at = _text(self.created_at, "routine created_at")
        self.updated_at = _text(self.updated_at, "routine updated_at")
        self.last_run_at = _optional_text(self.last_run_at, "last_run_at")
        self.last_run_status = _optional_text(self.last_run_status, "last_run_status")
        if self.last_run_status not in {None, "success", "failure"}:
            raise ValueError("last_run_status must be 'success', 'failure', or None")
        self.last_run_summary = _optional_text(self.last_run_summary, "last_run_summary")
        self.metadata = _dict(self.metadata, "routine metadata")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Routine":
        return cls(
            key=value.get("key", ""),
            description=value.get("description", ""),
            schedule=value.get("schedule"),
            steps=value.get("steps", []),
            enabled=value.get("enabled", True),
            created_at=value.get("created_at", ""),
            updated_at=value.get("updated_at", ""),
            last_run_at=value.get("last_run_at"),
            last_run_status=value.get("last_run_status"),
            last_run_summary=value.get("last_run_summary"),
            metadata=value.get("metadata", {}),
        )


@dataclass
class Milestone:
    key: str
    description: str
    evidence: List[str]
    achieved_at: Optional[str]
    created_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.key = _key(self.key, "milestone key")
        self.description = _text(self.description, "milestone description")
        self.evidence = _string_list(self.evidence, "milestone evidence")
        self.achieved_at = _optional_text(self.achieved_at, "achieved_at")
        self.created_at = _text(self.created_at, "milestone created_at")
        self.metadata = _dict(self.metadata, "milestone metadata")

    @property
    def achieved(self) -> bool:
        return self.achieved_at is not None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Milestone":
        return cls(
            key=value.get("key", ""),
            description=value.get("description", ""),
            evidence=value.get("evidence", []),
            achieved_at=value.get("achieved_at"),
            created_at=value.get("created_at", ""),
            metadata=value.get("metadata", {}),
        )


def derive_life_stage(state: "VelaState") -> LifeStage:
    """Derive a stage from capability and outcome evidence.

    ``memory`` is an internal baseline and deliberately does not count as a
    worldly capability.  Time, message count, and a user-assigned number are
    not inputs.  The thresholds are intentionally conservative: a new stage
    follows useful capability plus evidence that Vela has actually used it.
    """

    worldly_capabilities = {
        key for key, capability in state.capabilities.items()
        if capability.status is CapabilityStatus.AVAILABLE and key not in {"memory", "scheduler"}
    }
    successful_tasks = sum(outcome.status == "success" for outcome in state.task_outcomes)
    learned_skills = len(state.learned_skills)
    achieved_milestones = sum(milestone.achieved for milestone in state.milestones.values())

    if (
        len(worldly_capabilities) >= 3
        and learned_skills >= 2
        and successful_tasks >= 5
        and achieved_milestones >= 2
    ):
        return LifeStage.MATURE
    if (
        len(worldly_capabilities) >= 2
        and (learned_skills >= 1 or successful_tasks >= 2 or achieved_milestones >= 1)
    ):
        return LifeStage.TEEN
    if worldly_capabilities or successful_tasks or learned_skills or achieved_milestones:
        return LifeStage.CHILD
    return LifeStage.NEWBORN


@dataclass
class VelaState:
    """The complete serializable state for one Vela instance."""

    birth: BirthMetadata
    last_updated_at: str
    memories: Dict[str, Memory] = field(default_factory=dict)
    preferences: Dict[str, Preference] = field(default_factory=dict)
    people: Dict[str, Person] = field(default_factory=dict)
    capabilities: Dict[str, Capability] = field(default_factory=dict)
    learned_skills: Dict[str, LearnedSkill] = field(default_factory=dict)
    task_outcomes: List[TaskOutcome] = field(default_factory=list)
    routines: Dict[str, Routine] = field(default_factory=dict)
    milestones: Dict[str, Milestone] = field(default_factory=dict)
    extensions: Dict[str, Any] = field(default_factory=dict)
    schema_version: int = CURRENT_SCHEMA_VERSION

    @classmethod
    def new(
        cls,
        *,
        born_at: Optional[str] = None,
        first_message_id: Optional[str] = None,
        cause: str = "first_message",
        clock: Optional[Clock] = None,
        known_capabilities: Optional[Iterable[str]] = None,
        available_capabilities: Optional[Iterable[str]] = None,
    ) -> "VelaState":
        now = born_at or _timestamp(clock)
        state = cls(
            birth=BirthMetadata(now, cause=cause, first_message_id=first_message_id),
            last_updated_at=now,
        )
        # Memory is the only claim the core can make without an external
        # integration.  Other defaults are merely known-but-missing.
        state.register_capability(
            "memory",
            available=True,
            description="persistent conversation memory",
            source="vela-core",
            now=now,
        )
        for name in known_capabilities or ():
            if _key(name, "capability key") not in state.capabilities:
                state.register_capability(
                    name,
                    available=False,
                    requires_authorization=True,
                    reason_missing="not connected",
                    now=now,
                )
        for name in available_capabilities or ():
            state.register_capability(name, available=True, now=now)
        state.last_updated_at = now
        return state

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, now: Optional[str] = None) -> "VelaState":
        if not isinstance(value, Mapping):
            raise StateFormatError("state root must be an object")
        version = value.get("schema_version", 1)
        if not isinstance(version, int) or version < 1:
            raise StateFormatError("schema_version must be a positive integer")
        if version > CURRENT_SCHEMA_VERSION:
            raise StateFormatError(
                f"state schema {version} is newer than supported schema {CURRENT_SCHEMA_VERSION}"
            )
        try:
            state = cls(
                birth=BirthMetadata.from_dict(value.get("birth", {})),
                last_updated_at=value.get("last_updated_at", now or ""),
                memories={
                    item.key: item
                    for item in (Memory.from_dict(item_value) for item_value in _object_values(value.get("memories", {}), "memories"))
                },
                preferences={
                    item.key: item
                    for item in (Preference.from_dict(item_value) for item_value in _object_values(value.get("preferences", {}), "preferences"))
                },
                people={
                    item.key: item
                    for item in (Person.from_dict(item_value) for item_value in _object_values(value.get("people", {}), "people"))
                },
                capabilities={
                    item.key: item
                    for item in (Capability.from_dict(item_value) for item_value in _object_values(value.get("capabilities", {}), "capabilities"))
                },
                learned_skills={
                    item.key: item
                    for item in (LearnedSkill.from_dict(item_value) for item_value in _object_values(value.get("learned_skills", {}), "learned_skills"))
                },
                task_outcomes=[TaskOutcome.from_dict(item) for item in _list_of_objects(value.get("task_outcomes", []), "task_outcomes")],
                routines={
                    item.key: item
                    for item in (Routine.from_dict(item_value) for item_value in _object_values(value.get("routines", {}), "routines"))
                },
                milestones={
                    item.key: item
                    for item in (Milestone.from_dict(item_value) for item_value in _object_values(value.get("milestones", {}), "milestones"))
                },
                extensions=_dict(value.get("extensions", {}), "extensions"),
                schema_version=CURRENT_SCHEMA_VERSION,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateFormatError(f"invalid state document: {exc}") from exc
        if not state.last_updated_at:
            state.last_updated_at = now or state.birth.born_at
        return state

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "birth": self.birth.to_dict(),
            "last_updated_at": self.last_updated_at,
            "memories": {key: value.to_dict() for key, value in sorted(self.memories.items())},
            "preferences": {key: value.to_dict() for key, value in sorted(self.preferences.items())},
            "people": {key: value.to_dict() for key, value in sorted(self.people.items())},
            "capabilities": {key: value.to_dict() for key, value in sorted(self.capabilities.items())},
            "learned_skills": {key: value.to_dict() for key, value in sorted(self.learned_skills.items())},
            "task_outcomes": [item.to_dict() for item in self.task_outcomes],
            "routines": {key: value.to_dict() for key, value in sorted(self.routines.items())},
            "milestones": {key: value.to_dict() for key, value in sorted(self.milestones.items())},
            "extensions": copy.deepcopy(self.extensions),
        }

    def copy(self) -> "VelaState":
        return VelaState.from_dict(copy.deepcopy(self.to_dict()))

    @property
    def life_stage(self) -> LifeStage:
        return derive_life_stage(self)

    @property
    def birth_time(self) -> str:
        """Return Vela's birth timestamp without using it for stage derivation."""

        return self.birth.born_at

    @property
    def birth_metadata(self) -> BirthMetadata:
        return copy.deepcopy(self.birth)

    def capability_snapshot(self) -> Dict[str, List[str]]:
        return {
            "available": self.available_capabilities,
            "missing": self.missing_capabilities,
            "learned_skills": sorted(self.learned_skills),
        }

    @property
    def available_capabilities(self) -> List[str]:
        return sorted(key for key, value in self.capabilities.items() if value.status is CapabilityStatus.AVAILABLE)

    @property
    def missing_capabilities(self) -> List[str]:
        return sorted(key for key, value in self.capabilities.items() if value.status is CapabilityStatus.MISSING)

    @property
    def learned_skill_names(self) -> List[str]:
        return sorted(self.learned_skills)

    def _touch(self, now: Optional[str]) -> str:
        value = now or utc_now()
        self.last_updated_at = value
        return value

    def remember(
        self,
        key: str,
        content: str,
        *,
        source: str = "conversation",
        confidence: float = 1.0,
        tags: Optional[Iterable[str]] = None,
        now: Optional[str] = None,
    ) -> Memory:
        identifier = _key(key, "memory key")
        timestamp = self._touch(now)
        existing = self.memories.get(identifier)
        item = Memory(
            key=identifier,
            content=content,
            created_at=existing.created_at if existing else timestamp,
            updated_at=timestamp,
            source=source,
            confidence=confidence,
            tags=list(tags) if tags is not None else (existing.tags if existing else []),
        )
        self.memories[identifier] = item
        return copy.deepcopy(item)

    add_memory = remember

    def set_preference(
        self,
        key: str,
        value: Any,
        *,
        source: str = "conversation",
        now: Optional[str] = None,
    ) -> Preference:
        identifier = _key(key, "preference key")
        item = Preference(identifier, value, self._touch(now), source=source)
        self.preferences[identifier] = item
        return copy.deepcopy(item)

    def add_person(
        self,
        key: str,
        name: str,
        *,
        relationship: Optional[str] = None,
        notes: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> Person:
        identifier = _key(key, "person key")
        existing = self.people.get(identifier)
        item = Person(
            key=identifier,
            name=name,
            relationship=relationship if relationship is not None else (existing.relationship if existing else None),
            notes=list(notes) if notes is not None else (existing.notes if existing else []),
            updated_at=self._touch(now),
            metadata=metadata if metadata is not None else (existing.metadata if existing else {}),
        )
        self.people[identifier] = item
        return copy.deepcopy(item)

    def register_capability(
        self,
        name: str,
        *,
        available: bool,
        description: Optional[str] = None,
        reason_missing: Optional[str] = None,
        requires_authorization: Optional[bool] = None,
        source: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> Capability:
        identifier = _key(name, "capability key")
        timestamp = self._touch(now)
        existing = self.capabilities.get(identifier)
        status = CapabilityStatus.AVAILABLE if available else CapabilityStatus.MISSING
        authorization = (
            requires_authorization
            if requires_authorization is not None
            else (existing.requires_authorization if existing else False)
        )
        item = Capability(
            key=identifier,
            status=status,
            description=description or (existing.description if existing else identifier.replace("-", " ")),
            reason_missing=None if available else (reason_missing or (existing.reason_missing if existing else "not connected")),
            requires_authorization=authorization,
            source=source if source is not None else (existing.source if existing else None),
            updated_at=timestamp,
            granted_at=timestamp if available else None,
            metadata=metadata if metadata is not None else (existing.metadata if existing else {}),
        )
        self.capabilities[identifier] = item
        return copy.deepcopy(item)

    def set_capability_available(self, name: str, **kwargs: Any) -> Capability:
        kwargs["available"] = True
        return self.register_capability(name, **kwargs)

    grant_capability = set_capability_available

    def set_capability_missing(
        self,
        name: str,
        *,
        reason_missing: str = "not connected",
        description: Optional[str] = None,
        requires_authorization: bool = True,
        source: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> Capability:
        return self.register_capability(
            name,
            available=False,
            description=description,
            reason_missing=reason_missing,
            requires_authorization=requires_authorization,
            source=source,
            metadata=metadata,
            now=now,
        )

    require_capability = set_capability_missing

    def has_capability(self, name: str) -> bool:
        item = self.capabilities.get(_key(name, "capability key"))
        return item is not None and item.status is CapabilityStatus.AVAILABLE

    def learn_skill(
        self,
        name: str,
        description: str,
        *,
        steps: Optional[Iterable[str]] = None,
        required_capabilities: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> LearnedSkill:
        identifier = _key(name, "skill key")
        timestamp = self._touch(now)
        existing = self.learned_skills.get(identifier)
        item = LearnedSkill(
            key=identifier,
            description=description,
            steps=list(steps) if steps is not None else (existing.steps if existing else []),
            required_capabilities=list(required_capabilities) if required_capabilities is not None else (existing.required_capabilities if existing else []),
            created_at=existing.created_at if existing else timestamp,
            updated_at=timestamp,
            successful_uses=existing.successful_uses if existing else 0,
            last_used_at=existing.last_used_at if existing else None,
            metadata=metadata if metadata is not None else (existing.metadata if existing else {}),
        )
        self.learned_skills[identifier] = item
        return copy.deepcopy(item)

    def record_skill_use(self, name: str, *, successful: bool, now: Optional[str] = None) -> LearnedSkill:
        identifier = _key(name, "skill key")
        item = self.learned_skills.get(identifier)
        if item is None:
            raise KeyError(f"unknown learned skill: {identifier}")
        timestamp = self._touch(now)
        if successful:
            item.successful_uses += 1
            item.last_used_at = timestamp
        item.updated_at = timestamp
        return copy.deepcopy(item)

    def _record_task(
        self,
        *,
        task_id: Optional[str],
        task: str,
        status: str,
        summary: Optional[str],
        error: Optional[str],
        capabilities_used: Optional[Iterable[str]],
        metadata: Optional[Mapping[str, Any]],
        now: Optional[str],
    ) -> TaskOutcome:
        task_text = _text(task, "task")
        identifier = _key(task_id or task_text, "task_id")
        item = TaskOutcome(
            task_id=identifier,
            task=task_text,
            status=status,
            recorded_at=self._touch(now),
            summary=summary,
            error=error,
            capabilities_used=list(capabilities_used or []),
            metadata=metadata or {},
        )
        self.task_outcomes.append(item)
        return copy.deepcopy(item)

    def record_task_success(
        self,
        task: str,
        *,
        task_id: Optional[str] = None,
        summary: Optional[str] = None,
        capabilities_used: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> TaskOutcome:
        return self._record_task(
            task_id=task_id,
            task=task,
            status="success",
            summary=summary,
            error=None,
            capabilities_used=capabilities_used,
            metadata=metadata,
            now=now,
        )

    record_success = record_task_success

    def record_task_failure(
        self,
        task: str,
        *,
        task_id: Optional[str] = None,
        error: Optional[str] = None,
        summary: Optional[str] = None,
        capabilities_used: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> TaskOutcome:
        return self._record_task(
            task_id=task_id,
            task=task,
            status="failure",
            summary=summary,
            error=error,
            capabilities_used=capabilities_used,
            metadata=metadata,
            now=now,
        )

    record_failure = record_task_failure

    def record_task_exception(
        self,
        task: str,
        exception: BaseException,
        *,
        task_id: Optional[str] = None,
        capabilities_used: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> TaskOutcome:
        """Record a failure without raising or serializing a traceback."""

        return self.record_task_failure(
            task,
            task_id=task_id,
            error=f"{type(exception).__name__}: {exception}",
            capabilities_used=capabilities_used,
            metadata=metadata,
            now=now,
        )

    @property
    def successful_tasks(self) -> List[TaskOutcome]:
        return [copy.deepcopy(item) for item in self.task_outcomes if item.status == "success"]

    @property
    def failed_tasks(self) -> List[TaskOutcome]:
        return [copy.deepcopy(item) for item in self.task_outcomes if item.status == "failure"]

    def upsert_routine(
        self,
        name: str,
        description: str,
        *,
        schedule: Optional[str] = None,
        steps: Optional[Iterable[str]] = None,
        enabled: bool = True,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> Routine:
        identifier = _key(name, "routine key")
        existing = self.routines.get(identifier)
        timestamp = self._touch(now)
        item = Routine(
            key=identifier,
            description=description,
            schedule=schedule if schedule is not None else (existing.schedule if existing else None),
            steps=list(steps) if steps is not None else (existing.steps if existing else []),
            enabled=enabled,
            created_at=existing.created_at if existing else timestamp,
            updated_at=timestamp,
            last_run_at=existing.last_run_at if existing else None,
            last_run_status=existing.last_run_status if existing else None,
            last_run_summary=existing.last_run_summary if existing else None,
            metadata=metadata if metadata is not None else (existing.metadata if existing else {}),
        )
        self.routines[identifier] = item
        return copy.deepcopy(item)

    add_routine = upsert_routine

    def record_routine_run(
        self,
        name: str,
        *,
        successful: bool,
        summary: Optional[str] = None,
        now: Optional[str] = None,
    ) -> Routine:
        identifier = _key(name, "routine key")
        item = self.routines.get(identifier)
        if item is None:
            raise KeyError(f"unknown routine: {identifier}")
        timestamp = self._touch(now)
        item.last_run_at = timestamp
        item.last_run_status = "success" if successful else "failure"
        item.last_run_summary = summary
        item.updated_at = timestamp
        return copy.deepcopy(item)

    def record_milestone(
        self,
        name: str,
        description: str,
        *,
        evidence: Optional[Iterable[str]] = None,
        achieved: bool = True,
        metadata: Optional[Mapping[str, Any]] = None,
        now: Optional[str] = None,
    ) -> Milestone:
        identifier = _key(name, "milestone key")
        existing = self.milestones.get(identifier)
        timestamp = self._touch(now)
        item = Milestone(
            key=identifier,
            description=description,
            evidence=list(evidence) if evidence is not None else (existing.evidence if existing else []),
            achieved_at=(existing.achieved_at if existing and existing.achieved_at else timestamp) if achieved else None,
            created_at=existing.created_at if existing else timestamp,
            metadata=metadata if metadata is not None else (existing.metadata if existing else {}),
        )
        self.milestones[identifier] = item
        return copy.deepcopy(item)

    add_milestone = record_milestone


def _object_values(value: Any, label: str) -> List[Mapping[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        result = list(value.values())
    elif isinstance(value, list):
        result = value
    else:
        raise StateFormatError(f"{label} must be an object or list")
    if not all(isinstance(item, Mapping) for item in result):
        raise StateFormatError(f"{label} entries must be objects")
    return result


def _list_of_objects(value: Any, label: str) -> List[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise StateFormatError(f"{label} must be a list of objects")
    return value


class VelaStateStore:
    """Atomic JSON persistence and convenience mutation API.

    ``path`` should normally point inside the persistent Hermes home, for
    example ``/var/lib/hermes/vela-state.json`` in the container.  The class
    creates a state with only ``memory`` available when the file is absent;
    integrations must explicitly grant browser, calendar, mail, or other
    capabilities after authorization.
    """

    def __init__(
        self,
        path: Union[str, os.PathLike[str]],
        *,
        clock: Optional[Clock] = None,
        known_capabilities: Optional[Iterable[str]] = None,
        available_capabilities: Optional[Iterable[str]] = None,
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._known_capabilities = tuple(known_capabilities or ())
        self._available_capabilities = tuple(available_capabilities or ())
        self._lock = threading.RLock()

    def _new_state(self) -> VelaState:
        return VelaState.new(
            clock=self._clock,
            known_capabilities=self._known_capabilities,
            available_capabilities=self._available_capabilities,
        )

    def load(self) -> VelaState:
        """Load state, creating and atomically persisting a birth if absent."""

        with self._lock:
            if not self.path.exists():
                state = self._new_state()
                self.save(state)
                return state
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise StatePersistenceError(f"could not read Vela state at {self.path}") from exc
            try:
                return VelaState.from_dict(payload, now=_timestamp(self._clock))
            except StatePersistenceError:
                raise
            except (TypeError, ValueError, KeyError) as exc:
                raise StateFormatError(f"invalid Vela state at {self.path}: {exc}") from exc

    load_or_create = load

    def save(self, state: VelaState) -> None:
        """Write a complete state document with an atomic same-directory replace."""

        if not isinstance(state, VelaState):
            raise TypeError("state must be a VelaState")
        payload = state.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                os.chmod(handle.name, 0o600)
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary_path), str(self.path))
            temporary_path = None
            # Directory fsync is supported on Linux and harmlessly skipped on
            # filesystems (notably Windows) that do not expose it.
            try:
                directory_fd = os.open(str(self.path.parent), os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except (OSError, TypeError, ValueError) as exc:
            raise StatePersistenceError(f"could not atomically write Vela state at {self.path}") from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def snapshot(self) -> VelaState:
        return self.load()

    def update(self, mutator: Callable[[VelaState], Any]) -> Any:
        """Apply one mutation and persist only after the mutation succeeds.

        A failed mutator never writes a partial state.  The returned value is a
        detached copy when the mutator returns a state record, so callers do
        not accidentally mutate an object without persisting it.
        """

        if not callable(mutator):
            raise TypeError("mutator must be callable")
        with self._lock:
            # Avoid creating a birth file before a first mutation has
            # successfully completed.  ``load`` remains the explicit
            # load-or-create operation for callers that want that behavior.
            state = self.load() if self.path.exists() else self._new_state()
            result = mutator(state)
            self.save(state)
            return copy.deepcopy(result)

    def remember(self, key: str, content: str, **kwargs: Any) -> Memory:
        return self.update(lambda state: state.remember(key, content, now=_timestamp(self._clock), **kwargs))

    add_memory = remember

    def set_preference(self, key: str, value: Any, **kwargs: Any) -> Preference:
        return self.update(lambda state: state.set_preference(key, value, now=_timestamp(self._clock), **kwargs))

    def add_person(self, key: str, name: str, **kwargs: Any) -> Person:
        return self.update(lambda state: state.add_person(key, name, now=_timestamp(self._clock), **kwargs))

    def register_capability(self, name: str, **kwargs: Any) -> Capability:
        return self.update(lambda state: state.register_capability(name, now=_timestamp(self._clock), **kwargs))

    def set_capability_available(self, name: str, **kwargs: Any) -> Capability:
        return self.update(lambda state: state.set_capability_available(name, now=_timestamp(self._clock), **kwargs))

    grant_capability = set_capability_available

    def set_capability_missing(self, name: str, **kwargs: Any) -> Capability:
        return self.update(lambda state: state.set_capability_missing(name, now=_timestamp(self._clock), **kwargs))

    require_capability = set_capability_missing

    def learn_skill(self, name: str, description: str, **kwargs: Any) -> LearnedSkill:
        return self.update(lambda state: state.learn_skill(name, description, now=_timestamp(self._clock), **kwargs))

    def record_skill_use(self, name: str, **kwargs: Any) -> LearnedSkill:
        return self.update(lambda state: state.record_skill_use(name, now=_timestamp(self._clock), **kwargs))

    def record_task_success(self, task: str, **kwargs: Any) -> TaskOutcome:
        return self.update(lambda state: state.record_task_success(task, now=_timestamp(self._clock), **kwargs))

    record_success = record_task_success

    def record_task_failure(self, task: str, **kwargs: Any) -> TaskOutcome:
        return self.update(lambda state: state.record_task_failure(task, now=_timestamp(self._clock), **kwargs))

    record_failure = record_task_failure

    def record_task_exception(self, task: str, exception: BaseException, **kwargs: Any) -> TaskOutcome:
        return self.update(lambda state: state.record_task_exception(task, exception, now=_timestamp(self._clock), **kwargs))

    def upsert_routine(self, name: str, description: str, **kwargs: Any) -> Routine:
        return self.update(lambda state: state.upsert_routine(name, description, now=_timestamp(self._clock), **kwargs))

    add_routine = upsert_routine

    def record_routine_run(self, name: str, **kwargs: Any) -> Routine:
        return self.update(lambda state: state.record_routine_run(name, now=_timestamp(self._clock), **kwargs))

    def record_milestone(self, name: str, description: str, **kwargs: Any) -> Milestone:
        return self.update(lambda state: state.record_milestone(name, description, now=_timestamp(self._clock), **kwargs))

    add_milestone = record_milestone
