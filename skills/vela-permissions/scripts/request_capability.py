#!/usr/bin/env python3
"""Record an explicit, non-granting request for a missing capability."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


HERE = pathlib.Path(__file__).resolve()
SKILLS_ROOT = HERE.parents[2]
SHARED_SCRIPTS = SKILLS_ROOT / "vela-shared" / "scripts"
REGISTRY_PATH = SKILLS_ROOT / "vela-shared" / "references" / "capability-registry.json"
sys.path.insert(0, str(SHARED_SCRIPTS))

from state_bridge import update_state, utc_now  # noqa: E402


def _registry() -> dict[str, Any]:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"capability registry is unreadable: {exc}") from exc


def request_capability(capability: str, reason: str | None = None) -> dict[str, Any]:
    capability = capability.strip().lower()
    if not capability:
        raise SystemExit("capability must be nonblank")
    spec = _registry().get("capabilities", {}).get(capability)
    if not isinstance(spec, dict):
        raise SystemExit(f"unknown capability {capability!r}; add it to the registry first")
    reason = (reason or "").strip()[:500]
    result: dict[str, Any] = {}
    now = utc_now()

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal result
        available = state.get("capabilities", {}).get("available", {})
        if capability in available:
            result = {"status": "available", "capability": capability, "created": False}
            return state

        requests = state.setdefault("capability_requests", [])
        existing = next(
            (item for item in requests if isinstance(item, dict) and item.get("capability") == capability and item.get("status") in {"pending", "denied"}),
            None,
        )
        if existing is not None:
            if existing.get("status") == "denied":
                result = {
                    "status": "denied",
                    "capability": capability,
                    "created": False,
                    "request_id": existing.get("request_id"),
                }
            else:
                result = {
                    "status": "pending",
                    "capability": capability,
                    "created": False,
                    "request_id": existing.get("request_id"),
                }
            return state

        request_id = f"capability:{capability}"
        entry = {
            "request_id": request_id,
            "capability": capability,
            "status": "pending",
            "reason": reason,
            "request": spec.get("request"),
            "benefit": spec.get("benefit") or spec.get("description"),
            "requires_authorization": True,
            "requested_at": now,
        }
        requests.append(entry)
        result = {"status": "pending", "capability": capability, "created": True, "request_id": request_id}
        return state

    update_state(mutate)
    return result


def _message(result: dict[str, Any], reason: str | None) -> str:
    capability = result["capability"]
    if result["status"] == "available":
        return f"i can use {capability} now"
    if result["status"] == "denied":
        return f"i still don't have {capability} access; i won't ask again unless you bring it up"
    spec = _registry()["capabilities"][capability]
    request = spec.get("request") or f"connect {capability} access through Plow/Latch"
    benefit = spec.get("benefit") or spec.get("description")
    opening = "i need your help with something" if result.get("created") else "i'm still waiting on something"
    detail = f"{opening}: i don't have {capability} access yet."
    if reason:
        detail += f" {reason}."
    return f"{detail} if you want, you can {request}; {benefit} i won't connect it without you."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capability", required=True)
    parser.add_argument("--reason")
    parser.add_argument("--message", action="store_true")
    args = parser.parse_args(argv)
    result = request_capability(args.capability, args.reason)
    print(_message(result, args.reason) if args.message else json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

