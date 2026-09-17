#!/usr/bin/env python3
"""Manage Vela's opt-in, meaningful-only proactive event gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any


HERE = pathlib.Path(__file__).resolve()
SHARED_SCRIPTS = HERE.parents[2] / "vela-shared" / "scripts"
sys.path.insert(0, str(SHARED_SCRIPTS))

from state_bridge import add_milestone, load_state, update_state, utc_now  # noqa: E402


def enable(enabled: bool) -> dict[str, Any]:
    result: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        proactivity = state.setdefault("proactivity", {})
        was_enabled = bool(proactivity.get("enabled"))
        proactivity["enabled"] = enabled
        proactivity.setdefault("meaningful_only", True)
        proactivity.setdefault("sent_events", [])
        if enabled and not was_enabled:
            add_milestone(state, "proactivity-enabled", "proactivity")
        result.update({"enabled": enabled, "changed": was_enabled != enabled})
        return state

    update_state(mutate)
    return result


def _fingerprint(event: dict[str, Any]) -> str:
    stable = {
        "event_id": event.get("event_id"),
        "kind": event.get("kind"),
        "key": event.get("key"),
        "message": event.get("message"),
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def evaluate(event: dict[str, Any]) -> dict[str, Any]:
    state = load_state()
    proactivity = state.get("proactivity", {})
    if not proactivity.get("enabled"):
        return {"action": "quiet", "reason": "proactivity-disabled"}
    if proactivity.get("meaningful_only", True) and event.get("meaningful") is not True:
        return {"action": "quiet", "reason": "event-not-marked-meaningful"}
    message = str(event.get("message") or "").strip()
    if not message:
        return {"action": "quiet", "reason": "event-has-no-message"}
    required = event.get("required_capabilities") or []
    available = set(state.get("capabilities", {}).get("available", {}))
    missing = sorted(set(required) - available)
    if missing:
        return {"action": "quiet", "reason": "required-capability-missing", "missing": missing}
    fingerprint = _fingerprint(event)
    sent = proactivity.get("sent_events") or []
    if any(isinstance(item, dict) and item.get("fingerprint") == fingerprint for item in sent):
        return {"action": "quiet", "reason": "event-already-sent", "fingerprint": fingerprint}
    return {
        "action": "send",
        "message": message[:1000],
        "fingerprint": fingerprint,
        "event_id": event.get("event_id"),
        "kind": event.get("kind"),
    }


def mark_sent(fingerprint: str, event_id: str | None = None) -> dict[str, Any]:
    fingerprint = fingerprint.strip()
    if not fingerprint:
        raise SystemExit("fingerprint must be nonblank")

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        events = state.setdefault("proactivity", {}).setdefault("sent_events", [])
        if not any(isinstance(item, dict) and item.get("fingerprint") == fingerprint for item in events):
            events.append({"fingerprint": fingerprint, "event_id": event_id, "sent_at": utc_now()})
            del events[:-100]
        return state

    update_state(mutate)
    return {"marked": True, "fingerprint": fingerprint}


def _event(args: argparse.Namespace) -> dict[str, Any]:
    if args.stdin:
        try:
            value = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"stdin must be a JSON object: {exc}") from exc
    elif args.event_json:
        try:
            value = json.loads(args.event_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"--event-json must be JSON: {exc}") from exc
    else:
        value = {"meaningful": args.meaningful, "message": args.message, "kind": args.kind, "event_id": args.event_id}
    if not isinstance(value, dict):
        raise SystemExit("proactive event must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    for name, enabled in (("enable", True), ("disable", False)):
        command = sub.add_parser(name)
        command.set_defaults(handler=lambda _args, value=enabled: enable(value))

    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("--stdin", action="store_true")
    evaluate_parser.add_argument("--event-json")
    evaluate_parser.add_argument("--meaningful", action="store_true")
    evaluate_parser.add_argument("--message")
    evaluate_parser.add_argument("--kind")
    evaluate_parser.add_argument("--event-id")
    evaluate_parser.set_defaults(handler=lambda args: evaluate(_event(args)))

    mark_parser = sub.add_parser("mark-sent")
    mark_parser.add_argument("--fingerprint", required=True)
    mark_parser.add_argument("--event-id")
    mark_parser.set_defaults(handler=lambda args: mark_sent(args.fingerprint, args.event_id))

    args = parser.parse_args(argv)
    print(json.dumps(args.handler(args), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

