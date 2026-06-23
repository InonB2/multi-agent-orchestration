#!/usr/bin/env python3
"""
build_analytics.py — Generate dashboard/analytics_data.js from real dashboard inputs.

Inputs:
    tasks/active_tasks.json
    logs/ptme_decisions.jsonl (optional)
    dashboard/agent_activity.json (optional)
    logs/usage*.json (optional)
    BKM/AGENT_LESSONS.md (optional)

Output:
    dashboard/analytics_data.js with window.MMOI_ANALYTICS = {...};
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "tasks" / "active_tasks.json"
PTME_LOG_FILE = ROOT / "logs" / "ptme_decisions.jsonl"
ACTIVITY_FILE = ROOT / "dashboard" / "agent_activity.json"
LIVE_TASKS_FILE = ROOT / "dashboard" / "live_tasks.json"
LESSONS_FILE = ROOT / "BKM" / "AGENT_LESSONS.md"
LOGS_DIR = ROOT / "logs"
OUTPUT_FILE = ROOT / "dashboard" / "analytics_data.js"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rel(path: Path | str) -> str:
    """Render a path repo-relative (POSIX separators) so the published feed
    never embeds an absolute machine path. Falls back to the basename if the
    path lives outside the repo root."""
    p = Path(path)
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return p.name


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    token = str(value).strip().split()[0]
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
    seconds = (end_dt - start_dt).total_seconds()
    if seconds < 0:
        return None
    return int(seconds)


def duration_minutes(start: str | None, end: str | None) -> int | None:
    seconds = duration_seconds(start, end)
    if seconds is None:
        return None
    return seconds // 60


def format_runtime_display(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    if seconds == 0:
        return "0m"
    if seconds < 60:
        return "<1m"
    minutes = seconds // 60
    if minutes >= 60:
        hours = minutes // 60
        remainder = minutes % 60
        return f"{hours}h {remainder}m"
    return f"{minutes}m"


def normalize_name(value: str | None) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text.strip(" -_").lower()


def title_case_agent(agent_id: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[_-]+", agent_id) if part)


def split_assignees(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    cleaned = re.sub(r"\([^)]*\)", "", str(raw_value))
    cleaned = cleaned.replace("→", "+").replace("->", "+").replace("/", "+")
    parts = re.split(r"\s*\+\s*|\s*,\s*|\s+and\s+", cleaned)
    agents: list[str] = []
    for part in parts:
        token = part.strip()
        if not token:
            continue
        token = re.sub(r"[^A-Za-z0-9_-]", "", token)
        if token:
            agents.append(normalize_name(token))
    return agents


def infer_team(agent_id: str) -> str:
    agent = normalize_name(agent_id)
    if agent.startswith("codex"):
        return "Codex team"
    if agent.startswith("agy"):
        return "Agy team"
    return "Andy / Claude team"


def task_identity(task: dict) -> str:
    return str(task.get("task_id") or task.get("id") or task.get("title") or "unknown")


def read_json(path: Path, default: dict | list) -> dict | list:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def first_log_timestamp(lines: object, marker: str) -> str | None:
    if not isinstance(lines, list):
        return None
    for line in lines:
        text = str(line)
        if marker in text:
            return text.split()[0]
    return None


def primary_task_agent(task: dict) -> str | None:
    assignees = split_assignees(task.get("assigned_to") or task.get("agent"))
    return assignees[0] if assignees else None


def read_lessons(path: Path) -> dict:
    if not path.exists():
        return {"count": 0, "sections": [], "recent": [], "last_updated": None}
    sections: list[str] = []
    lessons: list[str] = []
    last_updated: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            sections.append(section)
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", section)
            if date_match:
                last_updated = date_match.group(1)
        elif line.startswith("- "):
            lessons.append(line[2:].strip())

    if last_updated is None:
        try:
            last_updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        except OSError:
            last_updated = None

    return {
        "count": len(lessons),
        "sections": sections,
        "recent": lessons[-5:],
        "last_updated": last_updated,
    }


def get_usage_number(payload: dict, *keys: str) -> int | float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return value
    return None


def normalize_usage(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None

    window_pct = get_usage_number(payload, "window_pct", "usage_pct", "session_usage_pct")
    tokens_used = get_usage_number(payload, "tokens_used", "total_tokens")
    token_budget = get_usage_number(payload, "token_budget", "budget_tokens")
    budget_remaining_tokens = get_usage_number(
        payload,
        "budget_remaining_tokens",
        "remaining_tokens",
        "token_budget_remaining",
    )
    budget_remaining_pct = get_usage_number(payload, "budget_remaining_pct", "remaining_pct")
    rate_limit_forecast = payload.get("rate_limit_forecast") or payload.get("forecast")

    if (
        window_pct is None
        and tokens_used is None
        and token_budget is None
        and budget_remaining_tokens is None
        and budget_remaining_pct is None
        and not rate_limit_forecast
    ):
        return None

    if budget_remaining_pct is None and token_budget and budget_remaining_tokens is not None:
        try:
            budget_remaining_pct = round((float(budget_remaining_tokens) / float(token_budget)) * 100, 1)
        except ZeroDivisionError:
            budget_remaining_pct = None

    return {
        "window_pct": window_pct,
        "tokens_used": tokens_used,
        "token_budget": token_budget,
        "budget_remaining_tokens": budget_remaining_tokens,
        "budget_remaining_pct": budget_remaining_pct,
        "rate_limit_forecast": rate_limit_forecast or None,
    }


def normalize_activity_entry(entry: dict) -> dict | None:
    agent = normalize_name(entry.get("agent"))
    if not agent:
        return None
    status = "running" if entry.get("status") == "running" else "idle"
    usage = normalize_usage(entry.get("usage"))
    return {
        "agent": agent,
        "task_id": entry.get("task_id"),
        "current_task": entry.get("current_task"),
        "status": status,
        "started_at": entry.get("started_at"),
        "updated_at": entry.get("updated_at"),
        "model": entry.get("model"),
        "effort": entry.get("effort"),
        "reason": entry.get("reason") or "",
        "usage": usage,
    }


def normalize_live_task_entry(entry: dict) -> dict | None:
    task_id = str(entry.get("task_id") or "").strip()
    worker_id = normalize_name(entry.get("worker_id"))
    if not task_id or not worker_id:
        return None
    usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else None
    return {
        "task_id": task_id,
        "worker_id": worker_id,
        "engine": entry.get("engine"),
        "status": entry.get("status") or "unknown",
        "task_text": entry.get("task_text") or "",
        "model": entry.get("model"),
        "effort": entry.get("effort"),
        "recommended_model": entry.get("recommended_model"),
        "recommended_effort": entry.get("recommended_effort"),
        "started_at": entry.get("started_at"),
        "updated_at": entry.get("updated_at"),
        "completed_at": entry.get("completed_at"),
        "decision_ref": entry.get("decision_ref"),
        "duration_seconds": get_usage_number(entry, "duration_seconds"),
        "usage": usage,
        "reason": entry.get("reason") or "",
    }


def build_live_task_lookup(payload: dict) -> tuple[list[dict], dict[str, dict], dict[str, dict]]:
    rows: list[dict] = []
    by_task_id: dict[str, dict] = {}
    by_worker_id: dict[str, dict] = {}
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized = normalize_live_task_entry(entry)
        if not normalized:
            continue
        rows.append(normalized)
        by_task_id[normalized["task_id"]] = normalized
        by_worker_id[normalized["worker_id"]] = normalized
    return rows, by_task_id, by_worker_id


def format_live_task_usage(usage: dict | None, duration_seconds: int | float | None) -> str | None:
    if isinstance(usage, dict):
        label = usage.get("label")
        if label:
            return str(label)
        tokens_used = get_usage_number(usage, "tokens_used")
        if tokens_used is not None:
            return f"{int(tokens_used):,} tokens"
        usage_duration = get_usage_number(usage, "duration_seconds")
        if usage_duration is not None:
            return format_runtime_display(int(usage_duration))
    if duration_seconds is not None:
        return format_runtime_display(int(duration_seconds))
    return None


def build_activity_lookup(payload: dict) -> tuple[dict[str, dict], int]:
    lookup: dict[str, dict] = {}
    entries = payload.get("entries", []) if isinstance(payload, dict) else []
    count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized = normalize_activity_entry(entry)
        if not normalized:
            continue
        lookup[normalized["agent"]] = normalized
        count += 1
    return lookup, count


def read_usage_logs(logs_dir: Path) -> tuple[list[str], dict[tuple[str, str | None], dict], dict[str, dict]]:
    if not logs_dir.exists():
        return [], {}, {}

    file_paths: list[str] = []
    by_agent_task: dict[tuple[str, str | None], dict] = {}
    by_agent: dict[str, dict] = {}

    for path in sorted(logs_dir.glob("usage*.json")):
        file_paths.append(_rel(path))
        payload = read_json(path, {})
        if isinstance(payload, dict):
            entries = payload.get("entries")
            if isinstance(entries, list):
                rows = entries
            else:
                rows = [payload]
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            agent = normalize_name(row.get("agent"))
            if not agent:
                continue
            usage = normalize_usage(row)
            if usage is None:
                continue
            task_id = row.get("task_id")
            usage["source_file"] = str(path)
            by_agent_task[(agent, str(task_id) if task_id else None)] = usage
            by_agent[agent] = usage

    return file_paths, by_agent_task, by_agent


def detect_task_runtime(task: dict, activity_lookup: dict[str, dict]) -> tuple[int | None, str | None, str | None]:
    start = task.get("claimed_at") or first_log_timestamp(task.get("coordinator_log"), "CLAIMED")
    end = task.get("completed_at") or first_log_timestamp(task.get("coordinator_log"), "COMPLETE")
    runtime_seconds = duration_seconds(start, end)
    if runtime_seconds is not None:
        return runtime_seconds, start, end

    agent = primary_task_agent(task)
    if not agent:
        return None, start, end

    live_entry = activity_lookup.get(agent)
    if live_entry and live_entry.get("status") == "running":
        live_task_id = str(live_entry.get("task_id") or "")
        current_task = normalize_name(live_entry.get("current_task"))
        title_match = current_task and current_task == normalize_name(task.get("title"))
        id_match = live_task_id and live_task_id == task_identity(task)
        if title_match or id_match:
            runtime_seconds = duration_seconds(live_entry.get("started_at"), live_entry.get("updated_at"))
            if runtime_seconds is not None:
                return runtime_seconds, live_entry.get("started_at"), live_entry.get("updated_at")

    return None, start, end


def build_sources_summary(
    tasks: list[dict],
    activity_count: int,
    running_agents: int,
    ptme_rows: list[dict],
    lessons: dict,
    usage_files: list[str],
    live_task_rows: list[dict],
) -> dict:
    status_counts = Counter()
    complexity_counts = Counter()
    tasks_with_complexity = 0

    for task in tasks:
        status = str(task.get("status") or "unknown")
        status_counts[status] += 1
        complexity = task.get("complexity")
        if complexity:
            complexity_counts[str(complexity)] += 1
            tasks_with_complexity += 1

    return {
        "tasks_total": len(tasks),
        "task_status_counts": dict(sorted(status_counts.items())),
        "task_complexity_counts": dict(sorted(complexity_counts.items())),
        "tasks_with_complexity": tasks_with_complexity,
        "tasks_missing_complexity": len(tasks) - tasks_with_complexity,
        "activity_entries": activity_count,
        "running_agents": running_agents,
        "live_task_count": len(live_task_rows),
        "ptme_decision_count": len(ptme_rows),
        "usage_log_file_count": len(usage_files),
        "usage_log_files": usage_files,
        "lessons_count": lessons.get("count", 0),
    }


def build_decisions(ptme_rows: list[dict], task_lookup: dict[str, dict], live_tasks_by_task: dict[str, dict]) -> dict:
    rows: list[dict] = []
    accepted = 0
    overridden = 0
    by_complexity = Counter()
    by_decided_model = Counter()
    by_decided_effort = Counter()

    for row in ptme_rows:
        task_id = str(row.get("task_id") or "")
        complexity = row.get("complexity")
        recommended_model = row.get("recommended_model")
        recommended_effort = row.get("recommended_effort")
        decided_model = row.get("decided_model")
        decided_effort = row.get("decided_effort")
        changed = bool(
            recommended_model != decided_model or recommended_effort != decided_effort
        )

        if (recommended_model is not None or recommended_effort is not None
                or decided_model is not None or decided_effort is not None):
            if changed:
                overridden += 1
            else:
                accepted += 1

        if complexity:
            by_complexity[str(complexity)] += 1
        if decided_model:
            by_decided_model[str(decided_model)] += 1
        if decided_effort:
            by_decided_effort[str(decided_effort)] += 1

        task = task_lookup.get(task_id, {})
        live_task = live_tasks_by_task.get(task_id, {})
        rows.append(
            {
                "task_id": task_id,
                "title": task.get("title"),
                "ts": row.get("ts") or row.get("timestamp"),
                "complexity": str(complexity) if complexity else None,
                "recommended_model": recommended_model,
                "recommended_effort": recommended_effort,
                "decided_model": decided_model,
                "decided_effort": decided_effort,
                "decided_by": row.get("decided_by"),
                "reason": row.get("reason") or "",
                "changed": changed,
                "actual_usage_label": format_live_task_usage(
                    live_task.get("usage") if isinstance(live_task, dict) else None,
                    live_task.get("duration_seconds") if isinstance(live_task, dict) else None,
                ),
                "actual_usage": live_task.get("usage") if isinstance(live_task, dict) else None,
                "worker_id": live_task.get("worker_id") if isinstance(live_task, dict) else None,
            }
        )

    rows.sort(key=lambda item: item.get("ts") or "")

    return {
        "empty_state": None if rows else "no PTME decisions logged yet",
        "rows": rows,
        "summary": {
            "logged_count": len(rows),
            "accepted_count": accepted,
            "overridden_count": overridden,
            "by_complexity": dict(sorted(by_complexity.items())),
            "by_decided_model": dict(sorted(by_decided_model.items())),
            "by_decided_effort": dict(sorted(by_decided_effort.items())),
        },
    }


def build_live_tasks(rows: list[dict]) -> dict:
    if not rows:
        return {
            "empty_state": "no live tasks recorded yet",
            "rows": [],
        }

    def sort_key(item: dict) -> tuple[int, str]:
        is_running = 0 if item.get("status") == "running" else 1
        return (is_running, str(item.get("updated_at") or ""))

    sorted_rows = sorted(rows, key=sort_key, reverse=False)
    output_rows = []
    for row in sorted_rows[:18]:
        output_rows.append(
            {
                "task_id": row.get("task_id"),
                "worker_id": row.get("worker_id"),
                "engine": row.get("engine"),
                "status": row.get("status"),
                "model": row.get("model"),
                "effort": row.get("effort"),
                "started_at": row.get("started_at"),
                "updated_at": row.get("updated_at"),
                "completed_at": row.get("completed_at"),
                "duration_seconds": row.get("duration_seconds"),
                "usage": row.get("usage"),
                "usage_label": format_live_task_usage(row.get("usage"), row.get("duration_seconds")),
            }
        )

    return {
        "empty_state": None,
        "rows": output_rows,
    }


def build_runtime(
    tasks: list[dict],
    activity_lookup: dict[str, dict],
    decision_rows: list[dict],
) -> tuple[dict, dict[str, int], dict[str, set[str]]]:
    decision_task_ids = {str(row.get("task_id")) for row in decision_rows if row.get("task_id")}
    runtime_rows: list[dict] = []
    agent_seconds: dict[str, int] = defaultdict(int)
    agent_runtime_task_ids: dict[str, set[str]] = defaultdict(set)

    for task in tasks:
        task_id = task_identity(task)
        runtime_seconds, started_at, ended_at = detect_task_runtime(task, activity_lookup)
        if runtime_seconds is None:
            continue
        runtime_minutes = runtime_seconds // 60
        agent = primary_task_agent(task)
        normalized_agent = normalize_name(agent) if agent else None
        if normalized_agent:
            agent_seconds[normalized_agent] += runtime_seconds
            agent_runtime_task_ids[normalized_agent].add(task_id)
        runtime_rows.append(
            {
                "task_id": task_id,
                "title": task.get("title") or "",
                "status": task.get("status") or "unknown",
                "agent": normalized_agent,
                "runtime_minutes": runtime_minutes,
                "runtime_seconds": runtime_seconds,
                "runtime_display": format_runtime_display(runtime_seconds),
                "started_at": started_at,
                "ended_at": ended_at,
                "has_decision": task_id in decision_task_ids,
                "pre_ptme": task_id not in decision_task_ids,
            }
        )

    runtime_rows.sort(key=lambda item: item["task_id"])
    agent_rows = [
        {
            "agent": agent,
            "name": title_case_agent(agent),
            "team": infer_team(agent),
            "task_count": len(agent_runtime_task_ids[agent]),
            "total_runtime_minutes": seconds // 60,
            "total_runtime_seconds": seconds,
            "total_runtime_display": format_runtime_display(seconds),
        }
        for agent, seconds in sorted(agent_seconds.items())
    ]
    return {"tasks": runtime_rows, "agents": agent_rows}, agent_seconds, agent_runtime_task_ids


def pick_latest_decision(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return sorted(rows, key=lambda item: item.get("ts") or "")[-1]


def merge_usage(preferred: dict | None, fallback: dict | None) -> dict:
    base = {
        "window_pct": None,
        "tokens_used": None,
        "token_budget": None,
        "budget_remaining_tokens": None,
        "budget_remaining_pct": None,
        "rate_limit_forecast": None,
    }
    for source in (fallback or {}, preferred or {}):
        for key in base:
            value = source.get(key)
            if value is not None:
                base[key] = value
    return base


def build_per_agent_usage(
    tasks: list[dict],
    activity_lookup: dict[str, dict],
    decisions: dict,
    agent_seconds: dict[str, int],
    runtime_task_ids: dict[str, set[str]],
    usage_by_agent_task: dict[tuple[str, str | None], dict],
    usage_by_agent: dict[str, dict],
) -> dict:
    task_lookup = {task_identity(task): task for task in tasks}
    decision_rows = decisions.get("rows", [])
    decision_rows_by_task = {str(row.get("task_id")): row for row in decision_rows if row.get("task_id")}
    decision_rows_by_agent: dict[str, list[dict]] = defaultdict(list)
    tracked_task_ids_by_agent: dict[str, set[str]] = defaultdict(set)

    for task_id, decision_row in decision_rows_by_task.items():
        task = task_lookup.get(task_id)
        if not task:
            continue
        assignees = split_assignees(task.get("assigned_to") or task.get("agent"))
        for agent in assignees:
            decision_rows_by_agent[agent].append(decision_row)
            tracked_task_ids_by_agent[agent].add(task_id)

    for agent, task_ids in runtime_task_ids.items():
        tracked_task_ids_by_agent[agent].update(task_ids)

    rows: list[dict] = []
    for agent in sorted(tracked_task_ids_by_agent):
        tracked_task_ids = sorted(tracked_task_ids_by_agent[agent])
        pre_ptme_task_ids = [task_id for task_id in tracked_task_ids if task_id not in decision_rows_by_task]
        latest_decision = pick_latest_decision(decision_rows_by_agent.get(agent, []))
        activity_entry = activity_lookup.get(agent, {})
        active_task_id = activity_entry.get("task_id")
        activity_usage = activity_entry.get("usage") if isinstance(activity_entry, dict) else None
        usage = merge_usage(
            activity_usage,
            usage_by_agent_task.get(
                (agent, str(active_task_id) if active_task_id else None)
            ) or usage_by_agent.get(agent),
        )
        total_runtime_seconds = agent_seconds.get(agent)
        rows.append(
            {
                "agent": agent,
                "name": title_case_agent(agent),
                "team": infer_team(agent),
                "task_count": len(tracked_task_ids),
                "tracked_task_ids": tracked_task_ids,
                "total_runtime_minutes": None if total_runtime_seconds is None else total_runtime_seconds // 60,
                "total_runtime_seconds": total_runtime_seconds,
                "total_runtime_display": format_runtime_display(total_runtime_seconds),
                "pre_ptme": bool(pre_ptme_task_ids),
                "pre_ptme_task_ids": pre_ptme_task_ids,
                "model": (
                    (activity_entry.get("model") if activity_entry.get("status") == "running" else None)
                    or (latest_decision or {}).get("decided_model")
                ),
                "effort": (
                    (activity_entry.get("effort") if activity_entry.get("status") == "running" else None)
                    or (latest_decision or {}).get("decided_effort")
                ),
                "usage": usage,
            }
        )

    return {"rows": rows}


def build_learning_loop(lessons: dict, decisions: dict) -> dict:
    decision_count = decisions.get("summary", {}).get("logged_count", 0)
    return {
        "lessons_count": lessons.get("count", 0),
        "last_updated": lessons.get("last_updated"),
        "recent_sections": lessons.get("sections", [])[-3:],
        "recent_lessons": lessons.get("recent", []),
        "decision_logging_status": "recording" if decision_count else "no PTME decisions logged yet",
        "qa_rounds": "metric pending — needs more logged runs",
        "rework_trend": "metric pending — needs more logged runs",
    }


def build_payload() -> dict:
    tasks_doc = read_json(TASKS_FILE, {"tasks": []})
    tasks = tasks_doc.get("tasks") if isinstance(tasks_doc, dict) and isinstance(tasks_doc.get("tasks"), list) else []
    task_lookup = {task_identity(task): task for task in tasks}

    ptme_rows = read_jsonl(PTME_LOG_FILE)
    activity_doc = read_json(ACTIVITY_FILE, {"entries": []})
    live_tasks_doc = read_json(LIVE_TASKS_FILE, {"entries": []})
    activity_lookup, activity_count = build_activity_lookup(activity_doc if isinstance(activity_doc, dict) else {})
    live_task_rows, live_tasks_by_task, _live_tasks_by_worker = build_live_task_lookup(
        live_tasks_doc if isinstance(live_tasks_doc, dict) else {}
    )
    running_agents = sum(1 for entry in activity_lookup.values() if entry.get("status") == "running")
    lessons = read_lessons(LESSONS_FILE)
    usage_files, usage_by_agent_task, usage_by_agent = read_usage_logs(LOGS_DIR)

    sources = build_sources_summary(
        tasks,
        activity_count,
        running_agents,
        ptme_rows,
        lessons,
        usage_files,
        live_task_rows,
    )
    decisions = build_decisions(ptme_rows, task_lookup, live_tasks_by_task)
    live_tasks = build_live_tasks(live_task_rows)
    runtime, agent_seconds, runtime_task_ids = build_runtime(tasks, activity_lookup, decisions.get("rows", []))
    per_agent_usage = build_per_agent_usage(
        tasks,
        activity_lookup,
        decisions,
        agent_seconds,
        runtime_task_ids,
        usage_by_agent_task,
        usage_by_agent,
    )
    learning_loop = build_learning_loop(lessons, decisions)

    return {
        "_meta": {
            "schema": 2,
            "generated_at": now_iso(),
            "source_files": {
                "tasks": _rel(TASKS_FILE),
                "ptme_decisions": _rel(PTME_LOG_FILE),
                "activity": _rel(ACTIVITY_FILE),
                "live_tasks": _rel(LIVE_TASKS_FILE),
                "lessons": _rel(LESSONS_FILE),
                "usage_logs": usage_files,
            },
        },
        "sources": sources,
        "runtime": runtime,
        "live_tasks": live_tasks,
        "decisions": decisions,
        "per_agent_usage": per_agent_usage,
        "learning_loop": learning_loop,
    }


def write_output(payload: dict, path: Path = OUTPUT_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    source = "window.MMOI_ANALYTICS = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";\n"
    path.write_text(source, encoding="utf-8")


def main(_: list[str] | None = None) -> int:
    payload = build_payload()
    write_output(payload, OUTPUT_FILE)
    print(f"[analytics] wrote {OUTPUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
