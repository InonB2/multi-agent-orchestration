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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import agent_activity
import codex_usage
import ptme

try:  # Phase 3/4 intelligence layer — optional, guarded.
    import learning_loop
except Exception:  # pragma: no cover - defensive
    learning_loop = None  # type: ignore[assignment]
try:
    import router as semantic_router
except Exception:  # pragma: no cover - defensive
    semantic_router = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
PTME_LOG_FILE = ROOT / "logs" / "ptme_decisions.jsonl"
USAGE_LOG_FILE = ROOT / "logs" / "usage.jsonl"
LIVE_TASKS_FILE = ROOT / "dashboard" / "live_tasks.json"
LIVE_TASKS_JS_FILE = ROOT / "dashboard" / "live_tasks.js"
VALID_ENGINES = ("agy", "codex", "claude")

# A live_tasks record left in status=running becomes a phantom "running" task on
# the dashboard if its worker died without a `complete` call. Two guards close
# such records (see expire_stale_live_tasks): (1) the worker is no longer marked
# running in agent_activity, or (2) the record is older than STALE_RUNNING_HOURS.
STALE_RUNNING_HOURS = 6
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
        token = token[len(engine) + 1 :]
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
        "worker": worker_id,
        "engine": None,
        "role": None,
        "assigned_name": None,
        "status": "pending",
        "model": None,
        "effort": None,
        "started_at": None,
        "updated_at": None,
        "completed_at": None,
        "finished_at": None,
        "decision_ref": None,
        "usage": None,
        "duration_seconds": None,
    }
    entries.append(entry)
    return entry


def _agent_running_now(worker_id: str) -> bool:
    """True if `worker_id` is currently marked running in agent_activity."""
    try:
        payload = agent_activity.read_activity(agent_activity.ACTIVITY_FILE)
    except SystemExit:  # corrupt activity file -> treat as not running
        return False
    for entry in payload.get("entries", []):
        if entry.get("agent") == worker_id:
            return str(entry.get("status")) == "running"
    return False


def expire_stale_live_tasks(payload: dict, now: str | None = None) -> int:
    """Close phantom running records so the panel never shows dead tasks.

    A record with status=running is stale (and flipped to done) when EITHER:
      - its worker is not currently running in agent_activity, OR
      - it has been running longer than STALE_RUNNING_HOURS.
    The record being completed normally (via complete()) is the matching record
    and is skipped by the caller before this runs, so live closes are untouched.

    Returns the number of records expired. Mutates `payload` in place.
    """
    now = now or now_iso()
    now_dt = parse_iso(now)
    expired = 0
    for entry in payload.get("entries", []):
        if str(entry.get("status")) != "running":
            continue
        worker_id = entry.get("worker_id") or entry.get("worker")
        started_dt = parse_iso(entry.get("started_at"))
        too_old = (
            now_dt is not None
            and started_dt is not None
            and (now_dt - started_dt) > timedelta(hours=STALE_RUNNING_HOURS)
        )
        running_now = bool(worker_id) and _agent_running_now(worker_id)
        if running_now and not too_old:
            continue
        entry["status"] = "done"
        entry["stale"] = True
        entry["completed_at"] = entry.get("completed_at") or now
        entry["finished_at"] = entry.get("finished_at") or entry.get("completed_at") or now
        entry["updated_at"] = now
        if entry.get("duration_seconds") is None:
            entry["duration_seconds"] = duration_seconds(entry.get("started_at"), now)
        reason = "stale: worker no longer running" if not running_now else "stale: exceeded {}h".format(STALE_RUNNING_HOURS)
        entry["reason"] = (entry.get("reason") or "") + (" | " if entry.get("reason") else "") + reason
        expired += 1
    return expired


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


def _annotate_latest_ptme_for_task(task_id: str, updates: dict) -> None:
    """Fallback when no decision_ref ts is known: update the latest row."""
    rows = ptme._load_records(PTME_LOG_FILE)
    target = None
    for row in rows:
        if row.get("task_id") == task_id:
            target = row  # keep last
    if target is None:
        return
    target.update(updates)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    agent_activity.atomic_write_text(PTME_LOG_FILE, text)


def _latest_ptme_record_for_task(task_id: str) -> dict | None:
    """Return the most recent ptme decision row for a task id (or None)."""
    target = None
    for row in ptme._load_records(PTME_LOG_FILE):
        if row.get("task_id") == task_id:
            target = row
    return target


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


def resolve_usage(engine: str, usage_tokens: int | None, duration_ms: int | None, window_pct: float | None) -> tuple[int | None, int | None, float | None]:
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


TESTER_FOR_ROLE = {
    "researcher": "qa",
    "coder": "qa",
    "qa": "security",
    "security": "qa",
    "designer": "qa",
    "content": "qa",
    "data": "security",
    "web": "qa",
    "orchestrator": "qa",
}


def tester_role_for(role: str | None) -> str | None:
    if not role:
        return None
    return TESTER_FOR_ROLE.get(role, "qa")


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
        # Engine-scoped: decide() resolves the engine ladder itself, so we do
        # NOT precompute a model here (precomputing risked a cross-engine map).
        return ptme.decide(
            task_id=task_id,
            task_text=task_text,
            recommended_model=recommend_model,
            recommended_effort=recommend_effort,
            override_model=override_model,
            override_effort=override_effort,
            decided_by=decided_by,
            engine=engine,
            role=role,
            tester_role=tester_role_for(role),
        )
    finally:
        ptme.LOG_FILE = original_log_file


def resolve_engine_via_router(
    task_text: str,
    role: str | None,
    candidate_engines: list[str] | None = None,
) -> tuple[str, dict | None]:
    """Consult the semantic router for an engine when one isn't forced.

    Returns (engine, route_result). Falls back to 'claude' if the router is
    unavailable so dispatch never deadlocks. Backward-compatible: callers that
    pass an explicit engine never reach here.
    """
    if semantic_router is None:
        return "claude", None
    result = semantic_router.route(
        task_text=task_text,
        candidate_engines=candidate_engines,
        role=role,
    )
    return result["engine"], result


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
    # engine == "auto" means: no engine was forced -> consult the router.
    route_result = None
    if engine == "auto":
        engine, route_result = resolve_engine_via_router(task_text, role)
        if route_result is not None:
            decided_by = "{} (router: {})".format(decided_by, route_result.get("chosen_via"))
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
    assigned_name = decision.get("assigned_name")
    specialization = decision.get("specialization")
    activity_entry = annotate_activity_entry(
        worker_id,
        {
            "engine": engine,
            "role": role,
            "assigned_name": assigned_name,
            "specialization": specialization,
        },
    )

    payload = read_live_tasks(LIVE_TASKS_FILE)
    started_at = activity_entry.get("started_at") or now_iso()
    updated_at = activity_entry.get("updated_at") or now_iso()
    entry = find_live_task(payload, worker_id=worker_id, task_id=task_id)
    entry.update(
        {
            "task_id": task_id,
            "worker_id": worker_id,
            "worker": worker_id,
            "engine": engine,
            "role": role,
            "assigned_name": assigned_name,
            "specialization": specialization,
            "tester_role": decision.get("tester_role"),
            "tester_name": decision.get("tester_name"),
            "status": "running",
            "task_text": task_text,
            "model": decision["decided_model"],
            "effort": decision["decided_effort"],
            "recommended_model": decision.get("recommended_model"),
            "recommended_effort": decision.get("recommended_effort"),
            "planned_tokens": decision.get("planned_tokens"),
            "decided_by": decision.get("decided_by"),
            "started_at": started_at,
            "updated_at": updated_at,
            "completed_at": None,
            "finished_at": None,
            "decision_ref": decision_ref,
            "reason": decision.get("reason") or "",
            "duration_seconds": None,
            "usage": None,
            "stale": False,
        }
    )
    # Close any OTHER phantom running records (the just-started one is protected
    # because its worker was set running in agent_activity above).
    expire_stale_live_tasks(payload, now=updated_at)
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
    qa_verdict: str | None = None,
    qa_finding: str | None = None,
    qa_tester: str | None = None,
    qa_severity: str | None = None,
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
    entry["worker"] = worker_id
    entry["worker_id"] = worker_id
    entry["engine"] = engine
    entry["completed_at"] = completed_at
    entry["finished_at"] = completed_at
    entry["updated_at"] = completed_at
    entry["duration_seconds"] = elapsed_seconds
    entry["usage"] = usage
    entry["role"] = role
    entry["stale"] = False
    # Sweep sibling phantom running records (this one is already done above).
    expire_stale_live_tasks(payload, now=completed_at)
    payload["_meta"]["updated_at"] = completed_at
    write_live_tasks(payload, LIVE_TASKS_FILE)

    # Back-annotate the SAME ptme decision record with finished_at + ACTUALS so
    # planned-vs-actual is visible. decision_ref is "task_id@ts"; fall back to
    # the latest record for this task_id if no ref was stored.
    decision_ref = entry.get("decision_ref")
    decision_ts = None
    if decision_ref and "@" in str(decision_ref):
        decision_ts = str(decision_ref).split("@", 1)[1]
    # Codex tokens are whole-session cumulative; per-task engines report task tokens.
    if engine == "codex":
        tokens_task = None
        tokens_session_cumulative = usage_tokens
    else:
        tokens_task = usage_tokens
        tokens_session_cumulative = None
    ptme_updates = {
        "finished_at": completed_at,
        "actual_tokens": usage_tokens,
        "tokens_task": tokens_task,
        "tokens_session_cumulative": tokens_session_cumulative,
        "duration_ms": int(duration_ms) if duration_ms is not None else None,
        "usage_window": ({"window_pct": float(window_pct)} if window_pct is not None else None),
        "run_status": status,
    }
    if decision_ts:
        annotate_ptme_decision(task_id=task_id, ts=decision_ts, updates=ptme_updates)
    else:
        _annotate_latest_ptme_for_task(task_id, ptme_updates)

    # Record the QA/security verdict on the decision record (two-tier QA may
    # have already stamped internal_qa/external_qa; this stamps the verdict that
    # this complete() carries). Worker != tester is asserted before writing.
    if qa_verdict is not None or qa_finding is not None or qa_tester is not None:
        verdict_updates: dict = {}
        if qa_verdict is not None:
            verdict_updates["qa_verdict"] = str(qa_verdict).strip().lower()
        if qa_tester is not None:
            verdict_updates["tested_by"] = qa_tester
        if qa_finding is not None:
            verdict_updates["qa_finding"] = qa_finding
        if decision_ts:
            annotate_ptme_decision(task_id=task_id, ts=decision_ts, updates=verdict_updates)
        else:
            _annotate_latest_ptme_for_task(task_id, verdict_updates)

    # Phase 3: feed the closed learning loop with this completed outcome.
    # Guarded — a missing module or any failure must NOT break completion.
    if learning_loop is not None:
        try:
            final_record = _latest_ptme_record_for_task(task_id)
            if final_record:
                learning_loop.record_outcome_from_decision(final_record)
        except Exception:
            pass

    # CLOSE THE LEARNING LOOP INTO THE PROFILE: when a QA/security finding is
    # recorded against this worker's work, append a dated lesson to the worker's
    # own per-engine profile (agents/teams/<engine>/<role>.md). The tester is
    # the source; worker != tester is enforced (a worker cannot author a lesson
    # blaming itself via its own tester id). Guarded — never breaks completion.
    if learning_loop is not None and qa_finding:
        try:
            tester = str(qa_tester or "").strip().lower()
            # Never let the worker masquerade as its own tester.
            if tester != worker_id:
                learning_loop.record_lesson(
                    engine=engine,
                    role=role,
                    lesson=qa_finding,
                    source=(qa_tester or "qa-gate"),
                    severity=qa_severity,
                )
        except Exception:
            pass

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
        qa_verdict=args.qa_verdict,
        qa_finding=args.qa_finding,
        qa_tester=args.qa_tester,
        qa_severity=args.qa_severity,
    )
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track worker dispatches for the dashboard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Record a worker dispatch")
    start_parser.add_argument("--worker")
    start_parser.add_argument(
        "--engine",
        required=True,
        choices=VALID_ENGINES + ("auto",),
        help="Engine to dispatch to, or 'auto' to consult the semantic router.",
    )
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
    complete_parser.add_argument("--qa-verdict", help="QA/security verdict to stamp (pass/fail)")
    complete_parser.add_argument("--qa-finding", help="QA/security finding → written as a lesson to the worker's profile")
    complete_parser.add_argument("--qa-tester", help="The tester id (must differ from --worker)")
    complete_parser.add_argument("--qa-severity", help="Optional finding severity (low/med/high)")
    complete_parser.set_defaults(func=cmd_complete)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
