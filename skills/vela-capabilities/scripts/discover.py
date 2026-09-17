#!/usr/bin/env python3
"""Record an evidence-based Hermes/Plow/Latch capability observation."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import pathlib
import shutil
import sys
from typing import Any


HERE = pathlib.Path(__file__).resolve()
SKILLS_ROOT = HERE.parents[2]
SHARED_SCRIPTS = SKILLS_ROOT / "vela-shared" / "scripts"
REGISTRY_PATH = SKILLS_ROOT / "vela-shared" / "references" / "capability-registry.json"
sys.path.insert(0, str(SHARED_SCRIPTS))

from state_bridge import add_milestone, load_state, update_state, utc_now  # noqa: E402


def _load_registry(path: pathlib.Path = REGISTRY_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"capability registry is unreadable: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("capabilities"), dict):
        raise SystemExit("capability registry must contain a capabilities object")
    return value


def _json_value(value: str | None, label: str) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} must be JSON: {exc}") from exc


def _read_observation(args: argparse.Namespace) -> dict[str, Any]:
    value: Any = {}
    if args.stdin:
        try:
            value = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"stdin must be a JSON object: {exc}") from exc
    if args.observation_json:
        value = _json_value(args.observation_json, "--observation-json")
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise SystemExit("capability observation must be a JSON object")
    if args.tools_json is not None:
        value["tools"] = _json_value(args.tools_json, "--tools-json")
    if args.relay_skills_json is not None:
        value["relay_skills"] = _json_value(args.relay_skills_json, "--relay-skills-json")
    return value


def _items(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if isinstance(value, dict):
        value = value.get("items", value.get("skills", value.get("tools", [])))
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise SystemExit("observed tools and relay_skills must be lists")
    result: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            result.append({"name": item, "description": ""})
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("id") or item.get("tool") or "").strip()
            description = str(item.get("description") or item.get("summary") or "").strip()
            if name:
                result.append({"name": name, "description": description})
    return result


def _candidates(item: dict[str, str]) -> list[str]:
    name = item["name"].lower()
    parts = [name]
    parts.extend(part for part in name.replace(".", "_").split("__") if part)
    parts.extend(part for part in name.split("_") if part)
    if item.get("description"):
        parts.append(item["description"].lower())
    return parts


def _matches(items: list[dict[str, str]], patterns: list[str]) -> dict[str, str] | None:
    for item in items:
        for candidate in _candidates(item):
            if any(fnmatch.fnmatch(candidate, pattern.lower()) for pattern in patterns):
                return item
    return None


def _capability_detail(capability_id: str, spec: dict[str, Any], item: dict[str, str] | None) -> dict[str, Any]:
    return {
        "id": capability_id,
        "description": spec.get("description", ""),
        "source": "vela-core" if capability_id == "memory" else ("hermes-tool" if item and item.get("name", "").startswith(("browser", "terminal", "shell")) else "plow-latch"),
        "evidence": None if item is None else item["name"],
        "detected_at": utc_now(),
        "requires_authorization": bool(spec.get("requires_authorization", False)),
    }


def _runtime_tools() -> list[dict[str, str]]:
    hermes = os.environ.get("HERMES_BIN", "/opt/hermes/bin/hermes")
    if os.path.exists(hermes) or shutil.which(hermes):
        return [{"name": "hermes_cron", "description": "Hermes cron is installed in this runtime."}]
    return []


def discover(observation: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = registry or _load_registry()
    tools = _items(observation.get("tools")) + _runtime_tools()
    relay_skills = _items(observation.get("relay_skills"))
    observed = "tools" in observation or "relay_skills" in observation or bool(_runtime_tools())
    before = load_state()
    before_available = set((before.get("capabilities") or {}).get("available", {}))
    changes: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        capabilities = state.setdefault("capabilities", {})
        available = capabilities.setdefault("available", {})
        missing = capabilities.setdefault("missing", {})
        for capability_id, spec in registry["capabilities"].items():
            if spec.get("always_available"):
                evidence = None
                detail = _capability_detail(capability_id, spec, evidence)
            elif not observed:
                continue
            else:
                tool_match = _matches(tools, list(spec.get("tool_patterns", [])))
                skill_match = _matches(relay_skills, list(spec.get("relay_skill_patterns", [])))
                evidence = skill_match or tool_match
                if evidence is None:
                    missing[capability_id] = {
                        "id": capability_id,
                        "description": spec.get("description", ""),
                        "reason": "No matching Hermes tool or Plow/Latch relay skill was observed.",
                        "requires_authorization": bool(spec.get("requires_authorization", False)),
                        "request": spec.get("request"),
                        "benefit": spec.get("benefit"),
                        "detected_at": utc_now(),
                    }
                    continue
                detail = _capability_detail(capability_id, spec, evidence)

            was_missing = capability_id in missing
            available[capability_id] = detail
            missing.pop(capability_id, None)
            if capability_id not in before_available:
                changes.setdefault("newly_available", []).append(capability_id)
                add_milestone(state, "capability-unlocked", capability_id, evidence=detail.get("evidence"))
            if was_missing:
                changes.setdefault("resolved", []).append(capability_id)

        capabilities["last_discovered_at"] = utc_now()
        for request in state.get("capability_requests", []):
            if isinstance(request, dict) and request.get("capability") in available and request.get("status") == "pending":
                request["status"] = "satisfied"
                request["satisfied_at"] = utc_now()
        return state

    state = update_state(mutate)
    available = state.get("capabilities", {}).get("available", {})
    missing = state.get("capabilities", {}).get("missing", {})
    return {
        "available": sorted(available),
        "missing": sorted(missing),
        "changes": changes,
        "observed": observed,
    }


def _message(result: dict[str, Any]) -> str:
    available = ", ".join(result["available"]) or "none"
    missing = ", ".join(result["missing"]) or "none"
    changed = result.get("changes", {}).get("newly_available", [])
    prefix = f"i can use {', '.join(changed)} now. " if changed else ""
    return f"{prefix}available: {available}; missing: {missing}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--observation-json")
    parser.add_argument("--tools-json")
    parser.add_argument("--relay-skills-json")
    parser.add_argument("--message", action="store_true")
    args = parser.parse_args(argv)
    result = discover(_read_observation(args))
    print(_message(result) if args.message else json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
