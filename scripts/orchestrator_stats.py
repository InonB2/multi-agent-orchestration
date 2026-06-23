#!/usr/bin/env python3
"""
orchestrator_stats.py — emit the dashboard orchestrator-stats feed.

Writes dashboard/orchestrator_stats.json AND dashboard/orchestrator_stats.js
(the .js is `window.ORCH_STATS = {...};`, mirroring agent_activity.js). Uses
agent_activity.atomic_write_text so the live feed never freezes against a
Windows share-read lock.

The feed reports usage honestly per engine: the Antigravity (agy) engine
exposes NO token/usage telemetry (only wall-clock duration), so its usage_pct
is null with usage_source stating exactly that, rather than faking a number.

Idempotent: running it regenerates the feed from current state (agent_activity
entries, logs/ptme_decisions.jsonl, logs/orchestrator_lessons.jsonl, and live
codex usage).

Usage:
    python scripts/orchestrator_stats.py            # write the feed
    python scripts/orchestrator_stats.py --print    # write + print the JSON
    python scripts/orchestrator_stats.py --dry-run  # compute + print, no write
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import agent_activity
import codex_usage
import ptme

ROOT = Path(__file__).resolve().parent.parent
PTME_LOG_FILE = ROOT / "logs" / "ptme_decisions.jsonl"
LESSONS_LOG_FILE = ROOT / "logs" / "orchestrator_lessons.jsonl"
STATS_JSON_FILE = ROOT / "dashboard" / "orchestrator_stats.json"
STATS_JS_FILE = ROOT / "dashboard" / "orchestrator_stats.js"

# The four orchestrators rendered in the dashboard header.
ORCHESTRATORS = [
    {"id": "andy", "name": "Andy", "prefixes": ("andy",), "engine": None},
    {"id": "claude", "name": "Claude", "prefixes": ("claude-",), "engine": "claude"},
    {"id": "agy", "name": "Antigravity", "prefixes": ("agy", "agy-"), "engine": "agy"},
    {"id": "codex", "name": "Codex", "prefixes": ("codex", "codex-"), "engine": "codex"},
]

# Specialist roles each engine team can field. The top orchestrator's
# "team_size" is its roster of named specialist agents; set ORCH_TEAM_SIZE to
# however many agents your orchestrator persona manages.
ENGINE_TEAM_ROLES = (
    "researcher", "coder", "qa", "security",
    "designer", "content", "data", "web", "orchestrator",
)
ORCH_TEAM_SIZE = 14


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _belongs_to(agent_id: str, orch: dict) -> bool:
    """Attribute an agent_activity id to exactly one orchestrator.

    andy: exact "andy" only.
    claude: starts with "claude-".
    agy: == "agy" or starts with "agy-".
    codex: == "codex" or starts with "codex-" or "codex" + digit (codex-2, codex3).
    """
    aid = (agent_id or "").strip().lower()
    if not aid:
        return False
    if orch["id"] == "andy":
        return aid == "andy"
    if orch["id"] == "claude":
        return aid.startswith("claude-")
    if orch["id"] == "agy":
        return aid == "agy" or aid.startswith("agy-")
    if orch["id"] == "codex":
        return aid == "codex" or aid.startswith("codex-") or (aid.startswith("codex") and aid[5:6].isdigit())
    return False


def _running_now(entries: list[dict], orch: dict) -> int:
    count = 0
    for entry in entries:
        if entry.get("status") == "running" and _belongs_to(entry.get("agent", ""), orch):
            count += 1
    return count


def _attributed_orchestrator(record: dict) -> str | None:
    """Decide which orchestrator a ptme decision belongs to.

    Priority:
      1. explicit "orchestrator" field (written by sub_orchestrator.py),
      2. else infer from the engine/worker_id prefix; dispatch_worker rows
         without an orchestrator tag are attributed to Andy (he dispatched
         them directly), EXCEPT we still bucket engine usage by prefix for
         running_now. For tasks_done we honor the explicit tag first, then
         fall back to engine.
    """
    orch = record.get("orchestrator")
    if orch in ("andy", "claude", "agy", "codex"):
        return orch
    engine = (record.get("engine") or "").strip().lower()
    if engine in ("claude", "agy", "codex"):
        return engine
    worker = (record.get("worker_id") or "").strip().lower()
    for cand in ("claude", "agy", "codex"):
        if worker.startswith(cand):
            return cand
    return "andy"


def count_tasks_done(records: list[dict]) -> dict[str, int]:
    counts = {o["id"]: 0 for o in ORCHESTRATORS}
    for record in records:
        owner = _attributed_orchestrator(record)
        if owner in counts:
            counts[owner] += 1
    return counts


def count_learning_loops(path: Path = LESSONS_LOG_FILE) -> dict[str, int]:
    counts = {o["id"]: 0 for o in ORCHESTRATORS}
    if not path.exists():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        owner = (rec.get("orchestrator") or "").strip().lower()
        if owner in counts:
            counts[owner] += 1
    return counts


def _codex_usage_pct() -> tuple[float | None, str]:
    """Live codex window usage via codex_usage.read_latest_usage()."""
    try:
        usage = codex_usage.read_latest_usage()
    except Exception:
        return None, "none (codex usage unreadable)"
    pct = usage.get("window_pct")
    if pct is None:
        return None, "none (no recent codex session window found)"
    return float(pct), "codex session window"


def build_stats() -> dict:
    activity = agent_activity.read_activity(agent_activity.ACTIVITY_FILE)
    entries = activity.get("entries", [])
    ptme_records = ptme._load_records(PTME_LOG_FILE)

    tasks_done = count_tasks_done(ptme_records)
    learning = count_learning_loops()

    codex_pct, codex_src = _codex_usage_pct()

    orchestrators = []
    for orch in ORCHESTRATORS:
        if orch["id"] == "andy":
            team_size = ORCH_TEAM_SIZE
        else:
            team_size = len(ENGINE_TEAM_ROLES)

        if orch["id"] == "codex":
            usage_pct, usage_source = codex_pct, codex_src
        elif orch["id"] == "agy":
            usage_pct = None
            usage_source = "none (agy exposes no token/usage telemetry — only wall-clock duration)"
        elif orch["id"] == "claude":
            # No reliable live token telemetry exposed for the Claude team here.
            usage_pct = None
            usage_source = "none (claude team usage not exposed in local telemetry)"
        else:  # andy
            usage_pct = None
            usage_source = "none (orchestrator; usage tracked per engine team)"

        orchestrators.append(
            {
                "id": orch["id"],
                "name": orch["name"],
                "team_size": team_size,
                "running_now": _running_now(entries, orch),
                "tasks_done_total": tasks_done[orch["id"]],
                "learning_loops_total": learning[orch["id"]],
                "usage_pct": usage_pct,
                "usage_source": usage_source,
            }
        )

    return {
        "_meta": {"updated_at": now_iso()},
        "orchestrators": orchestrators,
    }


def stats_js_source(payload: dict) -> str:
    return "window.ORCH_STATS = " + json.dumps(payload, indent=2, ensure_ascii=False) + ";"


def write_stats(payload: dict) -> None:
    agent_activity.atomic_write_text(
        STATS_JSON_FILE,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    agent_activity.atomic_write_text(STATS_JS_FILE, stats_js_source(payload))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit dashboard orchestrator-stats feed")
    parser.add_argument("--print", action="store_true", dest="do_print", help="Print the JSON after writing")
    parser.add_argument("--dry-run", action="store_true", help="Compute and print only; do not write the feed")
    args = parser.parse_args(argv)

    payload = build_stats()
    if not args.dry_run:
        write_stats(payload)
    if args.do_print or args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
