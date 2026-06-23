#!/usr/bin/env python3
"""
dispatch_worker.py — track real worker dispatches for the dashboard.

Commands:
    python scripts/dispatch_worker.py start --engine codex --role qa --task-id B5-07 --text "..."
    python scripts/dispatch_worker.py complete --worker codex-qa --task-id B5-07 --status done --usage-tokens 1542
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import agent_activity
import codex_usage
import ptme

ROOT = Path(__file__).resolve().parent.parent
PTME_LOG_FILE = ROOT / "logs" / "ptme_decisions.jsonl"
USAGE_LOG_FILE = ROOT / "logs" / "usage.jsonl"
LIVE_TASKS_FILE = ROOT / "dashboard" / "live_tasks.json"
LIVE_TASKS_JS_FILE = ROOT / "dashboard" / "live_tasks.js"
VALID_ENGINES = ("agy", "codex", "claude")
VALID_ROLES = (
    "researcher",
    "coder",
    "qa",
    "security",
    "designer",
    "content",
    "data",
    "web",
    "orchestrator",
)
LEGACY_ROLE_ALIASES = {
    "dash": "web",
    "designer": "designer",
    "frontend": "web",
    "qa": "qa",
    "research": "researcher",
    "researcher": "researcher",
    "security": "security",
    "tester": "qa",
    "web": "web",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    token = str(value).strip()
    if token.endswith("Z"):
        token = token[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(token)
    except ValueError:
        return None


def duration_seconds(start: str | None, end: str | None) -> int | None:
    start_dt = parse_iso(start)
    end_dt = parse_iso(end)
    if not start_dt or not end_dt:
        return None
    seconds = int((end_dt - start_dt).total_seconds())
    return seconds if seconds >= 0 else None


def duration_seconds_from_ms(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 0:
        return None
    return max(0, int(value) // 1000)


def format_duration_label(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    if seconds >= 60:
        return f"{seconds // 60}m"
    return "<1m"


def normalize_role(role: str | None) -> str | None:
    if role is None:
        return None
    token = str(role).strip().lower()
    token = LEGACY_ROLE_ALIASES.get(token, token)
    if token not in VALID_ROLES:
        raise ValueError("Unsupported role '{}'".format(role))
    return token


def infer_role(worker_id: str | None, engine: str | None = None) -> str | None:
    token = str(worker_id or "").strip().lower()
    if not token:
        return None
    if engine and token.startswith(engine + "-"):
        token = token[len(engine) + 1:]
    token = token.split("-", 1)[0]
    try:
        return normalize_role(token)
    except ValueError:
        return None


def canonical_worker_id(engine: str, worker_id: str | None, role: str | None) -> tuple[str, str | None]:
    normalized_role = normalize_role(role) if role is not None else infer_role(worker_id, engine=engine)
    if normalized_role:
        return f"{engine}-{normalized_role}", normalized_role
    if worker_id:
        return str(worker_id).strip().lower(), None
    raise ValueError("worker_id or role is required")


def seed_live_tasks_data(timestamp: str | None = None) -> dict:
    ts = timestamp or now_iso()
    return {
        "_meta": {
            "schema": 1,
            "note": "Live tasks recorded by scripts/dispatch_worker.py for file:// dashboard rendering.",
            "updated_at": ts,
        },
        "entries": [],
    }


def live_tasks_js_source(payload: dict) -> str:
    return "window.LIVE_TASKS = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";"


def write_live_tasks(payload: dict, path: Path = LIVE_TASKS_FILE) -> None:
    agent_activity.atomic_write_text(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    js_path = LIVE_TASKS_JS_FILE if path == LIVE_TASKS_FILE else path.with_suffix(".js")
    agent_activity.atomic_write_text(js_path, live_tasks_js_source(payload))


def read_live_tasks(path: Path = LIVE_TASKS_FILE) -> dict:
    if not path.exists():
        payload = seed_live_tasks_data()
        write_live_tasks(payload, path)
        return payload
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = seed_live_tasks_data()
        write_live_tasks(payload, path)
        return payload
    if "_meta" not in payload or "entries" not in payload:
        payload = seed_live_tasks_data()
        write_live_tasks(payload, path)
        return payload
    return payload


def find_live_task(payload: dict, worker_id: str, task_id: str) -> dict:
    entries = payload.setdefault("entries", [])
    for entry in entries:
        if entry.get("worker_id") == worker_id and entry.get("task_id") == task_id:
            return entry
    entry = {
        "task_id": task_id,
        "worker_id": worker_id,
        "engine": None,
        "role": None,
        "status": "pending",
        "model": None,
        "effort": None,
        "started_at": None,
        "updated_at": None,
        "completed_at": None,
        "decision_ref": None,
        "usage": None,
        "duration_seconds": None,
    }
    entries.append(entry)
    return entry


def annotate_ptme_decision(task_id: str, ts: str, updates: dict) -> None:
    rows = ptme._load_records(PTME_LOG_FILE)
    changed = False
    for row in reversed(rows):
        if row.get("task_id") == task_id and row.get("ts") == ts:
            row.update(updates)
            changed = True
            break
    if not changed:
        return
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    agent_activity.atomic_write_text(PTME_LOG_FILE, text)


def annotate_activity_entry(agent_id: str, updates: dict) -> dict:
    payload = agent_activity.read_activity(agent_activity.ACTIVITY_FILE)
    entry = agent_activity.find_entry(payload, agent_id)
    entry.update(updates)
    agent_activity.write_activity(payload, agent_activity.ACTIVITY_FILE)
    return entry


def append_usage_log(record: dict, path: Path | None = None) -> None:
    path = path or USAGE_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def resolve_usage(
    engine: str,
    usage_tokens: int | None,
    duration_ms: int | None,
    window_pct: float | None,
) -> tuple[int | None, int | None, float | None]:
    if engine != "codex":
        return usage_tokens, duration_ms, window_pct
    if usage_tokens is not None and duration_ms is not None and window_pct is not None:
        return usage_tokens, duration_ms, window_pct
    inferred = codex_usage.read_latest_usage()
    if usage_tokens is None:
        usage_tokens = inferred.get("tokens")
    if duration_ms is None:
        duration_ms = inferred.get("duration_ms")
    if window_pct is None:
        window_pct = inferred.get("window_pct")
    return usage_tokens, duration_ms, window_pct


def write_decision(
    task_id: str,
    task_text: str,
    engine: str,
    worker_id: str,
    role: str | None,
    recommend_model: str | None = None,
    recommend_effort: str | None = None,
    override_model: str | None = None,
    override_effort: str | None = None,
    decided_by: str = "local_orchestrator",
) -> dict:
    original_log_file = ptme.LOG_FILE
    ptme.LOG_FILE = PTME_LOG_FILE
    try:
        complexity = ptme.classify_complexity(task_text)
        default_model, default_effort = ptme.recommend_for_complexity(complexity, family=engine)
        return ptme.decide(
            task_id=task_id,
            task_text=task_text,
            recommended_model=recommend_model or default_model,
            recommended_effort=recommend_effort or default_effort,
            override_model=override_model,
            override_effort=override_effort,
            decided_by=decided_by,
        )
    finally:
        ptme.LOG_FILE = original_log_file


def start(
    worker_id: str,
    engine: str,
    role: str | None,
    task_id: str,
    task_text: str,
    recommend_model: str | None = None,
    recommend_effort: str | None = None,
    override_model: str | None = None,
    override_effort: str | None = None,
    decided_by: str = "local_orchestrator",
) -> dict:
    if engine not in VALID_ENGINES:
        raise ValueError("Unsupported engine '{}'".format(engine))
    worker_id, role = canonical_worker_id(engine, worker_id, role)

    decision = write_decision(
        task_id=task_id,
        task_text=task_text,
        engine=engine,
        worker_id=worker_id,
        role=role,
        recommend_model=recommend_model,
        recommend_effort=recommend_effort,
        override_model=override_model,
        override_effort=override_effort,
        decided_by=decided_by,
    )
    annotate_ptme_decision(
        task_id=task_id,
        ts=decision["ts"],
        updates={"engine": engine, "worker_id": worker_id, "role": role},
    )
    decision_ref = "{}@{}".format(task_id, decision["ts"])
    agent_activity.set(
        agent=worker_id,
        model=decision["decided_model"],
        effort=decision["decided_effort"],
        task=task_text,
        status="running",
        reason=decision["reason"],
        task_id=task_id,
    )
    activity_entry = annotate_activity_entry(
        worker_id,
        {"engine": engine, "role": role},
    )

    payload = read_live_tasks(LIVE_TASKS_FILE)
    entry = find_live_task(payload, worker_id=worker_id, task_id=task_id)
    entry.update(
        {
            "task_id": task_id,
            "worker_id": worker_id,
            "engine": engine,
            "role": role,
            "status": "running",
            "task_text": task_text,
            "model": decision["decided_model"],
            "effort": decision["decided_effort"],
            "recommended_model": decision.get("recommended_model"),
            "recommended_effort": decision.get("recommended_effort"),
            "decided_by": decision.get("decided_by"),
            "started_at": activity_entry.get("started_at") or now_iso(),
            "updated_at": activity_entry.get("updated_at") or now_iso(),
            "completed_at": None,
            "decision_ref": decision_ref,
            "reason": decision.get("reason") or "",
            "duration_seconds": None,
            "usage": None,
        }
    )
    payload["_meta"]["updated_at"] = entry["updated_at"]
    write_live_tasks(payload, LIVE_TASKS_FILE)
    return entry


def complete(
    worker_id: str,
    task_id: str,
    status: str,
    usage_tokens: int | None = None,
    duration_ms: int | None = None,
    window_pct: float | None = None,
) -> dict:
    payload = read_live_tasks(LIVE_TASKS_FILE)
    entry = find_live_task(payload, worker_id=worker_id, task_id=task_id)
    completed_at = now_iso()
    started_at = entry.get("started_at")
    engine = entry.get("engine") or ("codex" if str(worker_id).startswith("codex") else None)
    role = entry.get("role") or infer_role(worker_id, engine=engine)
    usage_tokens, duration_ms, window_pct = resolve_usage(engine or "", usage_tokens, duration_ms, window_pct)
    elapsed_seconds = duration_seconds_from_ms(duration_ms)
    if elapsed_seconds is None:
        elapsed_seconds = duration_seconds(started_at, completed_at)

    usage: dict | None = None
    if elapsed_seconds is not None:
        usage = {
            "duration_seconds": elapsed_seconds,
            "label": format_duration_label(elapsed_seconds),
        }
    if duration_ms is not None:
        usage = usage or {}
        usage["duration_ms"] = int(duration_ms)
    if usage_tokens is not None:
        usage = usage or {}
        usage["tokens_used"] = int(usage_tokens)
        usage["label"] = "{:,} tokens".format(int(usage_tokens))
    if window_pct is not None:
        usage = usage or {}
        usage["window_pct"] = float(window_pct)

    entry["status"] = status
    entry["completed_at"] = completed_at
    entry["updated_at"] = completed_at
    entry["duration_seconds"] = elapsed_seconds
    entry["usage"] = usage
    entry["role"] = role
    payload["_meta"]["updated_at"] = completed_at
    write_live_tasks(payload, LIVE_TASKS_FILE)

    append_usage_log(
        {
            "task_id": task_id,
            "worker": worker_id,
            "engine": engine,
            "role": role,
            "tokens": usage_tokens,
            "duration_ms": duration_ms,
            "window_pct": window_pct,
            "ts": completed_at,
        }
    )
    agent_activity.clear(worker_id)
    return entry


def cmd_start(args: argparse.Namespace) -> int:
    entry = start(
        worker_id=args.worker,
        engine=args.engine,
        role=args.role,
        task_id=args.task_id,
        task_text=args.text,
        recommend_model=args.recommend_model,
        recommend_effort=args.recommend_effort,
        override_model=args.override_model,
        override_effort=args.override_effort,
        decided_by=args.by,
    )
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    entry = complete(
        worker_id=args.worker,
        task_id=args.task_id,
        status=args.status,
        usage_tokens=args.usage_tokens,
        duration_ms=args.duration_ms,
        window_pct=args.window_pct,
    )
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track worker dispatches for the dashboard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Record a worker dispatch")
    start_parser.add_argument("--worker")
    start_parser.add_argument("--engine", required=True, choices=VALID_ENGINES)
    start_parser.add_argument("--role", choices=VALID_ROLES)
    start_parser.add_argument("--task-id", required=True)
    start_parser.add_argument("--text", required=True)
    start_parser.add_argument("--recommend-model")
    start_parser.add_argument("--recommend-effort")
    start_parser.add_argument("--override-model")
    start_parser.add_argument("--override-effort")
    start_parser.add_argument("--by", default="local_orchestrator")
    start_parser.set_defaults(func=cmd_start)

    complete_parser = subparsers.add_parser("complete", help="Record worker completion")
    complete_parser.add_argument("--worker", required=True)
    complete_parser.add_argument("--task-id", required=True)
    complete_parser.add_argument("--status", required=True)
    complete_parser.add_argument("--usage-tokens", type=int)
    complete_parser.add_argument("--duration-ms", type=int)
    complete_parser.add_argument("--window-pct", type=float)
    complete_parser.set_defaults(func=cmd_complete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
