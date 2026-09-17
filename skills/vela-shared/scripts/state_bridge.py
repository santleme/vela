#!/usr/bin/env python3
"""Small, stable bridge from Vela skills to the persistent Vela core state.

The Vela core owns the product state. This module intentionally knows only the
two-call import contract ``load_state(path)`` / ``save_state(path, state)`` and
an equivalent optional CLI contract (``get`` / ``set`` with JSON on stdin and
stdout). It keeps local JSON as a development fallback so the skills remain
testable before the core image is present, but never falls back after a
configured bridge has failed.
"""

from __future__ import annotations

import contextlib
import copy
import datetime as _datetime
import importlib
import inspect
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterator, Mapping

try:  # Windows test runners do not provide fcntl; the container does.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - exercised by Windows itself
    fcntl = None  # type: ignore


DEFAULT_STATE_PATH = "/var/lib/hermes/vela/state.json"
SCHEMA_VERSION = 1


class StateBridgeError(RuntimeError):
    """The configured Vela core bridge could not be used."""


def utc_now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_state(now: str | None = None) -> dict[str, Any]:
    born = now or utc_now()
    return {
        "schema_version": SCHEMA_VERSION,
        "birth_time": born,
        "life_stage": "newborn",
        "memories": [],
        "capabilities": {
            "available": {
                "memory": {
                    "id": "memory",
                    "source": "vela-core",
                    "detected_at": born,
                }
            },
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
        "proactivity": {
            "enabled": False,
            "meaningful_only": True,
            "sent_events": [],
        },
    }


def _derive_life_stage(state: dict[str, Any]) -> str:
    """Derive narrative voice from real evidence, never elapsed time or XP."""

    available = set((state.get("capabilities") or {}).get("available", {}))
    worldly = available - {"memory", "scheduler"}
    successful = len((state.get("tasks") or {}).get("successful", []))
    learned = len(state.get("learned_skills") or {})
    milestones = sum(
        1 for item in state.get("milestones", [])
        if isinstance(item, dict) and item.get("kind")
    )
    if len(worldly) >= 3 and learned >= 2 and successful >= 5 and milestones >= 2:
        return "mature"
    if len(worldly) >= 2 and (learned >= 1 or successful >= 2 or milestones >= 1):
        return "teen"
    if worldly or successful or learned or milestones:
        return "child"
    return "newborn"


def ensure_state(state: Any, now: str | None = None) -> dict[str, Any]:
    """Return a minimally shaped state without deleting core-owned fields."""

    if not isinstance(state, dict):
        state = {}
    result = copy.deepcopy(state)
    defaults = _default_state(now)
    for key, value in defaults.items():
        if key not in result or result[key] is None:
            result[key] = copy.deepcopy(value)

    result.setdefault("schema_version", SCHEMA_VERSION)
    result.setdefault("capabilities", {})
    if not isinstance(result["capabilities"], dict):
        result["capabilities"] = {}
    result["capabilities"].setdefault("available", {})
    result["capabilities"].setdefault("missing", {})
    result["capabilities"].setdefault("last_discovered_at", None)
    result.setdefault("learned_skills", {})
    result.setdefault("capability_requests", [])
    result.setdefault("tasks", {"successful": [], "failed": []})
    result["tasks"].setdefault("successful", [])
    result["tasks"].setdefault("failed", [])
    result.setdefault("milestones", [])
    result.setdefault("proactivity", {})
    result["proactivity"].setdefault("enabled", False)
    result["proactivity"].setdefault("meaningful_only", True)
    result["proactivity"].setdefault("sent_events", [])
    result["life_stage"] = _derive_life_stage(result)
    return result


def state_path(path: str | os.PathLike[str] | None = None) -> pathlib.Path:
    return pathlib.Path(path or os.environ.get("VELA_STATE_PATH") or DEFAULT_STATE_PATH)


def _configured_cli() -> list[str] | None:
    value = os.environ.get("VELA_CORE_CLI", "").strip()
    return shlex.split(value) if value else None


def _run_cli(command: list[str], payload: Any = None) -> Any:
    argv = command
    try:
        process = subprocess.run(
            argv,
            input=None if payload is None else json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise StateBridgeError(f"Vela core CLI could not start: {exc}") from exc
    if process.returncode:
        detail = (process.stderr or process.stdout or "").strip()
        raise StateBridgeError(f"Vela core CLI failed ({process.returncode}): {detail}")
    output = (process.stdout or "").strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise StateBridgeError("Vela core CLI returned non-JSON state") from exc


def _core_module() -> Any | None:
    root = os.environ.get("VELA_CORE_ROOT", "").strip()
    if root and root not in sys.path:
        sys.path.insert(0, root)
    try:
        return importlib.import_module("vela_core")
    except ModuleNotFoundError as exc:
        if exc.name == "vela_core":
            return None
        raise StateBridgeError(f"Vela core import failed: {exc}") from exc
    except Exception as exc:  # the core exists but is unhealthy
        raise StateBridgeError(f"Vela core import failed: {type(exc).__name__}: {exc}") from exc


def _call_load(module: Any, path: pathlib.Path) -> Any:
    for name in ("load_state", "read_state", "get_state"):
        function = getattr(module, name, None)
        if not callable(function):
            continue
        try:
            return function(str(path))
        except TypeError:
            return function()
    raise StateBridgeError(
        "vela_core is installed but does not expose load_state(path)"
    )


def _call_save(module: Any, path: pathlib.Path, state: dict[str, Any]) -> Any:
    for name in ("save_state", "write_state", "put_state"):
        function = getattr(module, name, None)
        if not callable(function):
            continue
        try:
            parameters = list(inspect.signature(function).parameters.values())
        except (TypeError, ValueError):
            parameters = []
        if len(parameters) >= 2:
            first = parameters[0].name.lower()
            if "state" in first or first in {"value", "data"}:
                return function(state, str(path))
            return function(str(path), state)
        return function(state)
    raise StateBridgeError(
        "vela_core is installed but does not expose save_state(path, state)"
    )


def _reconcile_typed_core(module: Any, path: pathlib.Path, payload: dict[str, Any]) -> None:
    """Fill typed-core collections not covered by an older adapter export.

    The stable ``vela_core.load_state``/``save_state`` dictionary contract is
    intentionally the first integration point. During the MVP transition a
    core package may expose that contract before its compatibility serializer
    knows every extension collection. If the typed store is public, reconcile
    those collections through its public mutation methods rather than writing
    around the core JSON format.
    """

    store_class = getattr(module, "VelaStateStore", None)
    if not callable(store_class):
        return
    store = store_class(path)

    def mutate(core_state: Any) -> Any:
        learned = payload.get("learned_skills") or {}
        if isinstance(learned, Mapping):
            for key, item in learned.items():
                if not isinstance(item, Mapping):
                    continue
                skill_key = str(item.get("key") or item.get("id") or key)
                description = str(item.get("description") or item.get("name") or skill_key).strip()
                if not description:
                    continue
                existing = core_state.learned_skills.get(skill_key)
                core_state.learn_skill(
                    skill_key,
                    description,
                    steps=item.get("steps") or [],
                    required_capabilities=item.get("required_capabilities") or [],
                    metadata={
                        **(item.get("metadata") or {}),
                        **({"ready": item["ready"], "blocked_by": item["blocked_by"]} if "ready" in item or "blocked_by" in item else {}),
                        **({"last_result_summary": item["last_result_summary"]} if item.get("last_result_summary") else {}),
                    },
                    now=item.get("last_success_at") or item.get("updated_at") or utc_now(),
                )
                saved = core_state.learned_skills.get(skill_key)
                if saved is not None:
                    uses = item.get("successful_uses", item.get("success_count"))
                    if isinstance(uses, int) and uses >= 0:
                        saved.successful_uses = uses
                    saved.last_used_at = item.get("last_used_at") or item.get("last_success_at")

        existing_task_ids = {item.task_id for item in getattr(core_state, "task_outcomes", [])}
        tasks = []
        tasks.extend(payload.get("tasks", {}).get("successful", []) if isinstance(payload.get("tasks"), Mapping) else [])
        tasks.extend(payload.get("tasks", {}).get("failed", []) if isinstance(payload.get("tasks"), Mapping) else [])
        for item in tasks:
            if not isinstance(item, Mapping):
                continue
            task = str(item.get("task") or item.get("name") or item.get("skill_id") or "").strip()
            if not task:
                continue
            task_id = str(item.get("task_id") or f"vela:{item.get('kind', 'task')}:{item.get('skill_id', task)}:{item.get('at', '')}")
            if task_id in existing_task_ids:
                continue
            kwargs = {
                "task_id": task_id,
                "summary": item.get("summary"),
                "capabilities_used": item.get("capabilities_used") or [],
                "metadata": item.get("metadata") or {},
                "now": item.get("recorded_at") or item.get("at") or utc_now(),
            }
            if item.get("status") == "failure" or item in (payload.get("tasks", {}).get("failed", []) if isinstance(payload.get("tasks"), Mapping) else []):
                core_state.record_task_failure(task, error=item.get("error"), **kwargs)
            else:
                core_state.record_task_success(task, **kwargs)
            existing_task_ids.add(task_id)

        milestones = payload.get("milestones") or []
        if isinstance(milestones, Mapping):
            milestones = list(milestones.values())
        existing_milestones = set(getattr(core_state, "milestones", {}))
        for item in milestones:
            if not isinstance(item, Mapping):
                continue
            key = str(item.get("key") or item.get("id") or "").strip()
            if not key:
                continue
            kind = str(item.get("kind") or "milestone").strip()
            identifier = key if key in existing_milestones else f"{kind}:{key}"
            if identifier in existing_milestones:
                continue
            core_state.record_milestone(
                identifier,
                str(item.get("description") or kind),
                evidence=item.get("evidence") or ([key] if key else []),
                achieved=item.get("achieved", True),
                metadata=item.get("metadata") or {},
                now=item.get("at") or item.get("achieved_at") or utc_now(),
            )
            existing_milestones.add(identifier)
        return core_state

    store.update(mutate)


def _core_wrote_bridge_shape(path: pathlib.Path) -> bool:
    """Detect the current flat compatibility document before legacy repair."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(value, dict)
        and isinstance(value.get("capabilities"), dict)
        and "available" in value["capabilities"]
        and "missing" in value["capabilities"]
        and "tasks" in value
    )


@contextlib.contextmanager
def _file_lock(path: pathlib.Path) -> Iterator[None]:
    """Serialize local fallback updates; core implementations own their locks."""

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _load_file(path: pathlib.Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ensure_state({})
    except (OSError, json.JSONDecodeError) as exc:
        raise StateBridgeError(f"Vela state is unreadable at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise StateBridgeError(f"Vela state at {path} is not a JSON object")
    return ensure_state(raw)


def _save_file(path: pathlib.Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(ensure_state(state), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def load_state(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = state_path(path)
    cli = _configured_cli()
    if cli:
        value = _run_cli([*cli, "get", str(target)])
        return ensure_state(value or {})

    module = _core_module()
    if module is not None:
        return ensure_state(_call_load(module, target))

    with _file_lock(target):
        return _load_file(target)


def save_state(state: dict[str, Any], path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = state_path(path)
    normalized = ensure_state(state)
    cli = _configured_cli()
    if cli:
        _run_cli([*cli, "set", str(target)], normalized)
        return normalized

    module = _core_module()
    if module is not None:
        _call_save(module, target, normalized)
        # A transitional typed core may expose the stable functions before its
        # serializer handles every bridge collection. The current core writes
        # the complete flat contract itself, so only invoke the typed fallback
        # when the document on disk proves that it did not.
        if not _core_wrote_bridge_shape(target):
            _reconcile_typed_core(module, target, normalized)
        return normalized

    with _file_lock(target):
        _save_file(target, normalized)
    return normalized


def update_state(
    mutator: Callable[[dict[str, Any]], Any],
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Load, mutate and persist state, returning the normalized state."""

    target = state_path(path)
    cli = _configured_cli()
    module = None if cli else _core_module()
    if cli or module is not None:
        current = load_state(target)
        result = mutator(current)
        next_state = current if result is None else result
        return save_state(ensure_state(next_state), target)

    with _file_lock(target):
        current = _load_file(target)
        result = mutator(current)
        next_state = current if result is None else result
        normalized = ensure_state(next_state)
        _save_file(target, normalized)
        return normalized


def add_milestone(state: dict[str, Any], kind: str, key: str, **extra: Any) -> bool:
    """Append a milestone once; return whether it was newly added."""

    milestones = state.setdefault("milestones", [])
    if any(
        isinstance(item, dict)
        and (
            (item.get("kind") == kind and item.get("key") == key)
            or item.get("key") == f"{kind}:{key}"
        )
        for item in milestones
    ):
        return False
    milestones.append({"kind": kind, "key": key, "at": utc_now(), **extra})
    return True


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("get", "init"))
    parser.add_argument("--path", default=None)
    args = parser.parse_args(argv)
    if args.command == "get":
        print(json.dumps(load_state(args.path), sort_keys=True))
    else:
        print(json.dumps(save_state(load_state(args.path), args.path), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
