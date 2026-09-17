#!/usr/bin/env python3
"""Persist a successful repeatable workflow as a Vela learned skill."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any


HERE = pathlib.Path(__file__).resolve()
SHARED_SCRIPTS = HERE.parents[2] / "vela-shared" / "scripts"
sys.path.insert(0, str(SHARED_SCRIPTS))

from state_bridge import add_milestone, update_state, utc_now  # noqa: E402


def _json_arg(value: str | None, label: str, default: Any) -> Any:
    if value is None:
        return default
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label} must be JSON: {exc}") from exc
    return parsed


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "learned-workflow"


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise SystemExit(f"{label} must be a non-empty JSON list of nonblank strings")
    return [item.strip() for item in value]


def record(
    *,
    name: str,
    description: str,
    steps: list[str],
    required_capabilities: list[str],
    outcome: str,
    result_summary: str | None = None,
) -> dict[str, Any]:
    skill_id = _slug(name)
    now = utc_now()
    summary = (result_summary or "").strip()[:500]
    if outcome not in {"success", "failed"}:
        raise SystemExit("--outcome must be success or failed")

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        if outcome == "failed":
            failures = state.setdefault("tasks", {}).setdefault("failed", [])
            failures.append({
                "task_id": f"workflow-attempt:{skill_id}:{now}",
                "kind": "workflow-attempt",
                "task": name.strip(),
                "status": "failure",
                "summary": summary,
                "error": summary or "workflow attempt failed",
                "capabilities_used": required_capabilities,
                "recorded_at": now,
                "at": now,
            })
            del failures[:-100]
            return state

        available = set(state.get("capabilities", {}).get("available", {}))
        missing = sorted(set(required_capabilities) - available)
        skills = state.setdefault("learned_skills", {})
        existing = skills.get(skill_id)
        if isinstance(existing, dict):
            success_count = int(existing.get("success_count", existing.get("successful_uses", 0))) + 1
        else:
            success_count = 1
        skills[skill_id] = {
            "id": skill_id,
            "name": name.strip(),
            "description": description.strip(),
            "steps": steps,
            "required_capabilities": required_capabilities,
            "source": "user-taught",
            "learned_at": existing.get("learned_at", now) if isinstance(existing, dict) else now,
            "last_success_at": now,
            "success_count": success_count,
            "ready": not missing,
            "blocked_by": missing,
            "last_result_summary": summary,
        }
        successful = state.setdefault("tasks", {}).setdefault("successful", [])
        successful.append({
            "task_id": f"workflow:{skill_id}:{now}",
            "kind": "workflow",
            "skill_id": skill_id,
            "task": name.strip(),
            "status": "success",
            "summary": summary,
            "capabilities_used": required_capabilities,
            "recorded_at": now,
            "at": now,
        })
        del successful[:-100]
        if existing is None:
            add_milestone(state, "workflow-learned", skill_id, required_capabilities=required_capabilities)
        return state

    state = update_state(mutate)
    if outcome == "failed":
        return {"learned": False, "recorded_failure": True, "skill_id": skill_id}
    skill = state["learned_skills"][skill_id]
    return {
        "learned": True,
        "skill_id": skill_id,
        "ready": skill["ready"],
        "blocked_by": skill["blocked_by"],
        "success_count": skill["success_count"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--steps-json", required=True)
    parser.add_argument("--required-capabilities-json", default="[]")
    parser.add_argument("--outcome", required=True, choices=("success", "failed"))
    parser.add_argument("--result-summary")
    args = parser.parse_args(argv)
    name = args.name.strip()
    description = args.description.strip()
    if not name or not description:
        raise SystemExit("--name and --description must be nonblank")
    steps = _strings(_json_arg(args.steps_json, "--steps-json", []), "--steps-json")
    required = _json_arg(args.required_capabilities_json, "--required-capabilities-json", [])
    if not isinstance(required, list) or any(not isinstance(item, str) or not item.strip() for item in required):
        raise SystemExit("--required-capabilities-json must be a JSON list of strings")
    result = record(
        name=name,
        description=description,
        steps=steps,
        required_capabilities=[item.strip() for item in required],
        outcome=args.outcome,
        result_summary=args.result_summary,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
