#!/usr/bin/env python3
"""agent_telemetry.py - live AOA agent activity feed writer.

The dashboard (dashboard/agent_status.js) overlays any entry whose status is
"running" from dashboard/agent_activity.js onto its seeded role roster, keyed by
the entry's `agent` id (e.g. "codex-qa", "agy-content", "claude-orchestrator").
CLI agent dispatches previously wrote NO telemetry, so the dashboard showed no
activity. This script closes that gap: call `start` when you dispatch an agent and
`stop` when it finishes, and the dashboard reflects which agent is on which task
and for how long (started_at is rendered as elapsed client-side).

Writes BOTH dashboard/agent_activity.js (the window.AGENT_ACTIVITY wrapper the
dashboard loads) and dashboard/agent_activity.json (twin, for tooling).

Usage:
  python scripts/agent_telemetry.py start --agent codex-qa --task DASH-SPLIT \
      --desc "QA dashboard split" --model gpt-5-codex --role qa --effort high \
      --reason "verifying smoke test"
  python scripts/agent_telemetry.py heartbeat --agent codex-qa --reason "phase 2/3"
  python scripts/agent_telemetry.py stop --agent codex-qa --status done
  python scripts/agent_telemetry.py list
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import agent_activity as activity
import sidecar_lock

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
JS_PATH = DASH / "agent_activity.js"
JSON_PATH = DASH / "agent_activity.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _activity_path() -> Path:
    return Path(JSON_PATH)


def _save(doc: dict, path: Path | None = None) -> None:
    activity.write_activity(doc, path or _activity_path())


def _load(path: Path | None = None) -> dict:
    return activity.read_activity(path or _activity_path())


def _find(doc: dict, agent: str) -> dict | None:
    for entry in doc.get("entries", []):
        if str(entry.get("agent", "")).lower() == agent.lower():
            return entry
    return None


def _ensure_entry_shape(entry: dict, agent: str) -> None:
    seed = activity.idle_entry(agent)
    for key, value in seed.items():
        if key in entry:
            continue
        entry[key] = [] if isinstance(value, list) else value


def _duration_seconds(started_at: str | None, ended_at: str) -> int | None:
    if not started_at:
        return None
    try:
        started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        ended = datetime.strptime(ended_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, int((ended - started).total_seconds()))


def _locked_mutate(mutate_fn):
    path = _activity_path()
    return sidecar_lock.locked_update(
        path,
        mutate_fn,
        load_fn=lambda locked_path: _load(Path(locked_path)),
        save_fn=lambda doc, locked_path=None: _save(doc, Path(locked_path) if locked_path else path),
    )


def cmd_start(a):
    agent = a.agent.lower()

    def mutate(doc: dict) -> dict:
        entry = _find(doc, agent)
        now = _now()
        if entry is None:
            entry = activity.idle_entry(agent)
            doc.setdefault("entries", []).append(entry)
        _ensure_entry_shape(entry, agent)

        already_running = entry.get("status") == "running" and entry.get("task_id") == a.task
        started_at = entry.get("started_at") if already_running and entry.get("started_at") else now
        spawned_at = entry.get("spawned_at") if already_running and entry.get("spawned_at") else started_at

        entry.update({
            "agent": agent,
            "model": a.model,
            "effort": a.effort,
            "role": a.role,
            "current_task": a.desc,
            "task_id": a.task,
            "status": "running",
            "started_at": started_at,
            "spawned_at": spawned_at,
            "ended_at": None,
            "duration_sec": None,
            "updated_at": now,
            "reason": a.reason or "",
            "usage": None,
        })
        entry["session_usage_pct"] = int(entry.get("session_usage_pct") or 0)

        meta = doc.setdefault("_meta", {})
        meta["updated_at"] = now
        meta["generator"] = "scripts/agent_telemetry.py"
        meta["version"] = "2.0.0"
        return doc

    _locked_mutate(mutate)
    print(f"[telemetry] START {a.agent} -> {a.task} ({a.desc})")


def cmd_heartbeat(a):
    agent = a.agent.lower()

    def mutate(doc: dict) -> dict | None:
        entry = _find(doc, agent)
        if entry is None:
            return None
        _ensure_entry_shape(entry, agent)
        now = _now()
        entry["updated_at"] = now
        if a.reason:
            entry["reason"] = a.reason
        meta = doc.setdefault("_meta", {})
        meta["updated_at"] = now
        meta["generator"] = "scripts/agent_telemetry.py"
        meta["version"] = "2.0.0"
        return doc

    if _locked_mutate(mutate) is None:
        print(f"[telemetry] no entry for {a.agent}; use start first", file=sys.stderr)
        sys.exit(1)
    print(f"[telemetry] HEARTBEAT {a.agent}")


def cmd_stop(a):
    agent = a.agent.lower()

    def mutate(doc: dict) -> dict | None:
        entry = _find(doc, agent)
        if entry is None:
            return None
        _ensure_entry_shape(entry, agent)
        now = _now()
        entry["status"] = "idle"
        entry["current_task"] = None
        entry["task_id"] = None
        entry["ended_at"] = now
        entry["duration_sec"] = _duration_seconds(entry.get("started_at"), now)
        entry["updated_at"] = now
        entry["started_at"] = None
        entry["spawned_at"] = None
        entry["usage"] = None
        entry["session_usage_pct"] = int(entry.get("session_usage_pct") or 0)
        if a.reason:
            entry["reason"] = a.reason
        elif a.status:
            entry["reason"] = f"last run: {a.status}"
        else:
            entry["reason"] = ""

        meta = doc.setdefault("_meta", {})
        meta["updated_at"] = now
        meta["generator"] = "scripts/agent_telemetry.py"
        meta["version"] = "2.0.0"
        return doc

    if _locked_mutate(mutate) is None:
        print(f"[telemetry] no entry for {a.agent}", file=sys.stderr)
        sys.exit(0)
    print(f"[telemetry] STOP {a.agent} (status={a.status or 'idle'})")


def cmd_list(a):
    doc = _load()
    running = [e for e in doc["entries"] if e.get("status") == "running"]
    if not running:
        print("[telemetry] no agents running")
        return
    for e in running:
        print(
            f"  {e['agent']:<20} {e.get('task_id') or '-':<18} "
            f"since {e.get('started_at')}  | {e.get('current_task')}"
        )


def main():
    p = argparse.ArgumentParser(description="AOA agent activity telemetry writer")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start")
    s.add_argument("--agent", required=True, help="agent id, e.g. codex-qa / agy-content / claude-orchestrator")
    s.add_argument("--task", required=True, help="task_id")
    s.add_argument("--desc", required=True, help="human-readable current task")
    s.add_argument("--model", default=None)
    s.add_argument("--effort", default=None)
    s.add_argument("--role", default=None)
    s.add_argument("--reason", default=None)
    s.set_defaults(func=cmd_start)

    h = sub.add_parser("heartbeat")
    h.add_argument("--agent", required=True)
    h.add_argument("--reason", default=None)
    h.set_defaults(func=cmd_heartbeat)

    st = sub.add_parser("stop")
    st.add_argument("--agent", required=True)
    st.add_argument("--status", default=None, help="done|failed|... (for the reason line)")
    st.add_argument("--reason", default=None)
    st.set_defaults(func=cmd_stop)

    ls = sub.add_parser("list")
    ls.set_defaults(func=cmd_list)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
