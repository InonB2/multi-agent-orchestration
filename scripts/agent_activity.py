#!/usr/bin/env python3
"""
agent_activity.py — Maintain the live dashboard/agent_activity.json feed.

Commands:
    python scripts/agent_activity.py set --agent codex --model gpt-5 --effort high \
        --task "Fix dashboard" --status running --reason "Working the bug"
    python scripts/agent_activity.py clear --agent codex
    python scripts/agent_activity.py list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTIVITY_FILE = ROOT / "dashboard" / "agent_activity.json"
ACTIVITY_JS_FILE = ROOT / "dashboard" / "agent_activity.js"

# Generic seed roster — role-based placeholder ids so the dashboard renders
# out-of-the-box. Replace/extend with your own orchestrator + engine worker
# ids. The producer scripts and dashboard key off the engine prefix
# (claude- / agy- / codex-) and the role suffix, not these specific names.
SEED_AGENT_IDS = [
    "andy",  # top orchestrator (rename to your own orchestrator id)
    "claude-orchestrator",
    "claude-researcher",
    "claude-coder",
    "claude-qa",
    "claude-security",
    "agy",
    "agy-researcher",
    "agy-coder",
    "agy-qa",
    "codex",
    "codex-coder",
    "codex-qa",
    "codex-security",
]


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def idle_entry(agent: str, updated_at: str | None = None) -> dict:
    return {
        "agent": agent,
        "task_id": None,
        "model": None,
        "effort": None,
        "current_task": None,
        "status": "idle",
        "started_at": None,
        "updated_at": updated_at,
        "reason": "",
        "session_usage_pct": 0,
        "usage": None,
    }


def seed_activity_data(timestamp: str | None = None) -> dict:
    ts = timestamp or now_iso()
    return {
        "_meta": {
            "schema": 1,
            "note": "Live agent activity overlay. Idle entries fall back to dashboard/agent_status.js seed data.",
            "updated_at": ts,
        },
        "entries": [idle_entry(agent, updated_at=ts) for agent in SEED_AGENT_IDS],
    }


def activity_js_path(path: Path = ACTIVITY_FILE) -> Path:
    if path == ACTIVITY_FILE:
        return ACTIVITY_JS_FILE
    return path.with_suffix(".js")


def activity_js_source(payload: dict) -> str:
    return "window.AGENT_ACTIVITY = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";"


def atomic_write_text(path: Path, text: str) -> None:
    """Write text durably, resilient to the destination being locked.

    Uses a per-target temp name (so the .json and .js writes never share a
    temp file) and tries an atomic os.replace first. On Windows the rename can
    fail with PermissionError (WinError 5) when the destination is held open
    (e.g. the dashboard .js loaded in a browser / scanned by AV). In that case
    we retry briefly, then fall back to an in-place overwrite, which succeeds
    against a share-read lock where rename does not. This stops the live feed
    from silently freezing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")  # unique per target: .json.tmp / .js.tmp
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2 * (attempt + 1))
    # Destination locked against rename — overwrite in place as a fallback.
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def write_activity(payload: dict, path: Path = ACTIVITY_FILE) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    atomic_write_text(activity_js_path(path), activity_js_source(payload))


def read_activity(path: Path = ACTIVITY_FILE) -> dict:
    if not path.exists():
        payload = seed_activity_data()
        write_activity(payload, path)
        return payload
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[ERROR] Could not parse {path}: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if "_meta" not in payload or "entries" not in payload:
        payload = seed_activity_data()
        write_activity(payload, path)
        return payload
    return payload


def find_entry(payload: dict, agent: str) -> dict:
    for entry in payload.get("entries", []):
        if entry.get("agent") == agent:
            return entry
    entry = idle_entry(agent)
    payload.setdefault("entries", []).append(entry)
    return entry


def reset_entry(entry: dict, timestamp: str, reason: str = "") -> None:
    entry["task_id"] = None
    entry["model"] = None
    entry["effort"] = None
    entry["current_task"] = None
    entry["status"] = "idle"
    entry["started_at"] = None
    entry["updated_at"] = timestamp
    entry["reason"] = reason
    entry["session_usage_pct"] = int(entry.get("session_usage_pct") or 0)
    entry["usage"] = None


def set(
    agent: str,
    model: str,
    effort: str,
    task: str,
    status: str = "running",
    reason: str = "",
    task_id: str | None = None,
    usage: dict | None = None,
    path: Path | None = None,
) -> dict:
    path = path or ACTIVITY_FILE
    payload = read_activity(path)
    timestamp = now_iso()
    entry = find_entry(payload, agent)

    if status == "idle":
        reset_entry(entry, timestamp, reason=reason or "")
    else:
        previous_started_at = entry.get("started_at")
        previous_status = entry.get("status")
        entry["task_id"] = task_id
        entry["model"] = model
        entry["effort"] = effort
        entry["current_task"] = task
        entry["status"] = "running"
        entry["started_at"] = previous_started_at if previous_status == "running" and previous_started_at else timestamp
        entry["updated_at"] = timestamp
        entry["reason"] = reason or ""
        entry["session_usage_pct"] = int(entry.get("session_usage_pct") or 0)
        entry["usage"] = usage

    payload["_meta"]["updated_at"] = timestamp
    write_activity(payload, path)
    return entry


def clear(agent: str, reason: str = "", path: Path | None = None) -> dict:
    path = path or ACTIVITY_FILE
    payload = read_activity(path)
    timestamp = now_iso()
    entry = find_entry(payload, agent)
    reset_entry(entry, timestamp, reason=reason)
    payload["_meta"]["updated_at"] = timestamp
    write_activity(payload, path)
    return entry


def cmd_set(args: argparse.Namespace) -> int:
    entry = set(
        agent=args.agent,
        model=args.model,
        effort=args.effort,
        task=args.task,
        status=args.status,
        reason=args.reason,
        task_id=args.task_id,
    )
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    entry = clear(args.agent)
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    print(json.dumps(read_activity(ACTIVITY_FILE), indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage dashboard/agent_activity.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="Set a live agent activity entry")
    set_parser.add_argument("--agent", required=True)
    set_parser.add_argument("--model", required=True)
    set_parser.add_argument("--effort", required=True)
    set_parser.add_argument("--task", required=True)
    set_parser.add_argument("--status", required=True, choices=("running", "idle"))
    set_parser.add_argument("--reason", default="")
    set_parser.add_argument("--task-id")
    set_parser.set_defaults(func=cmd_set)

    clear_parser = subparsers.add_parser("clear", help="Reset an agent to idle")
    clear_parser.add_argument("--agent", required=True)
    clear_parser.set_defaults(func=cmd_clear)

    list_parser = subparsers.add_parser("list", help="Print the activity document")
    list_parser.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
