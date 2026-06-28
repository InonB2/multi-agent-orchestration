#!/usr/bin/env python3
"""
orchestrator_stats.py — emit the dashboard orchestrator-stats feed.

Writes dashboard/orchestrator_stats.json AND dashboard/orchestrator_stats.js
(the .js is `window.ORCH_STATS = {...};`, mirroring agent_activity.js). Uses
agent_activity.atomic_write_text so the live feed never freezes against a
Windows share-read lock.

The feed answers the question "why don't we see agy's usage%?" honestly:
agy exposes NO token/usage telemetry (only wall-clock duration), so its
usage_pct is null with usage_source saying exactly that.

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
import sys
from datetime import datetime, timezone
from pathlib import Path

import re

import agent_activity
import codex_usage
import ptme
import rate_wall_watchdog

# Honest per-engine usage reader (codex real / claude estimate / agy null).
_USAGE_BRIDGE_DIR = Path(__file__).resolve().parent / "usage_bridge"
if str(_USAGE_BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(_USAGE_BRIDGE_DIR))
import usage_bridge_reader  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PTME_LOG_FILE = ROOT / "logs" / "ptme_decisions.jsonl"
LESSONS_LOG_FILE = ROOT / "logs" / "orchestrator_lessons.jsonl"
ROSTER_FILE = ROOT / "agents" / "roster.md"
STATS_JSON_FILE = ROOT / "dashboard" / "orchestrator_stats.json"
STATS_JS_FILE = ROOT / "dashboard" / "orchestrator_stats.js"

# The four orchestrators rendered in the dashboard header.
ORCHESTRATORS = [
    {"id": "root", "name": "Root", "prefixes": ("root",), "engine": None},
    {"id": "claude", "name": "Claude", "prefixes": ("claude-",), "engine": "claude"},
    {"id": "agy", "name": "Antigravity", "prefixes": ("agy", "agy-"), "engine": "agy"},
    {"id": "codex", "name": "Codex", "prefixes": ("codex", "codex-"), "engine": "codex"},
]

# Specialist ROLES each engine team can field (BKM/worker_roster.md). These are
# role TEMPLATES cloned per engine — a different concept from Root's roster.
ENGINE_TEAM_ROLES = (
    "researcher", "coder", "qa", "security",
    "designer", "content", "data", "web", "orchestrator",
)
# Fallback only if the roster file cannot be read; the real count is read live.
ANDY_TEAM_SIZE_FALLBACK = 18


def count_roster_agents(path: Path = ROSTER_FILE) -> int:
    """Count named agents from agents/roster.md (live, never hardcoded).

    The roster is a markdown table; each agent is a row beginning with
    "| Name |". We count data rows, skipping the header and separator rows.
    """
    if not path.exists():
        return ANDY_TEAM_SIZE_FALLBACK
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        first = cells[0]
        # Skip header row and markdown separator rows (---).
        if first.lower() in ("name", "") or set(first) <= set("-: "):
            continue
        # A real agent row has a name in col 0 and a title in col 1.
        if len(cells) >= 2 and re.match(r"^[A-Za-z][A-Za-z0-9 _-]*$", first):
            names.add(first.lower())
    return len(names) or ANDY_TEAM_SIZE_FALLBACK


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _belongs_to(agent_id: str, orch: dict) -> bool:
    """Attribute an agent_activity id to exactly one orchestrator.

    root: exact "root" only.
    claude: starts with "claude-".
    agy: == "agy" or starts with "agy-".
    codex: == "codex" or starts with "codex-" or "codex" + digit (codex-2, codex3).
    """
    aid = (agent_id or "").strip().lower()
    if not aid:
        return False
    if orch["id"] == "root":
        return aid == "root"
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
         without an orchestrator tag are attributed to Root (he dispatched
         them directly), EXCEPT we still bucket engine usage by prefix for
         running_now. For tasks_done we honor the explicit tag first, then
         fall back to engine.
    """
    orch = record.get("orchestrator")
    if orch in ("root", "claude", "agy", "codex"):
        return orch
    engine = (record.get("engine") or "").strip().lower()
    if engine in ("claude", "agy", "codex"):
        return engine
    worker = (record.get("worker_id") or "").strip().lower()
    for cand in ("claude", "agy", "codex"):
        if worker.startswith(cand):
            return cand
    return "root"


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
    """Live codex PRIMARY (5h) window usage via codex_usage.read_latest_usage()."""
    try:
        usage = codex_usage.read_latest_usage()
    except Exception:
        return None, "none (codex usage unreadable)"
    pct = usage.get("window_pct")
    if pct is None:
        return None, "none (no recent codex session window found)"
    return float(pct), "codex primary (5h) session window"


def _codex_weekly_pct() -> tuple[float | None, str]:
    """Live codex SECONDARY (weekly) window via rate_wall_watchdog."""
    try:
        state = rate_wall_watchdog.read_codex_windows()
    except Exception:
        return None, "none (codex weekly window unreadable)"
    if not state.get("found"):
        return None, "none (no recent codex session with rate_limits)"
    secondary = state.get("windows", {}).get("secondary", {})
    pct = secondary.get("used_percent")
    if pct is None:
        return None, "none (codex secondary/weekly window not reported)"
    return float(pct), "codex secondary (weekly) window"


def _usage_details() -> dict:
    """Honest per-engine usage_detail map via usage_bridge_reader.

    Returns {engine: detail} for codex/claude/agy. Any read failure inside the
    reader already degrades to an honest-null detail; this wrapper additionally
    guards against the reader itself being unimportable/raising.
    """
    try:
        return usage_bridge_reader.read_all()
    except Exception:
        fallback_note = "usage reader unavailable; honest null"
        return {
            eng: {
                "tokens": None,
                "window_pct_primary": None,
                "window_pct_weekly": None,
                "source": "usage_bridge_reader (unavailable)",
                "confidence": "none",
                "note": fallback_note,
            }
            for eng in ("codex", "claude", "agy")
        }


def build_stats() -> dict:
    activity = agent_activity.read_activity(agent_activity.ACTIVITY_FILE)
    entries = activity.get("entries", [])
    ptme_records = ptme._load_records(PTME_LOG_FILE)

    tasks_done = count_tasks_done(ptme_records)
    learning = count_learning_loops()

    codex_primary_pct, codex_primary_src = _codex_usage_pct()
    codex_weekly_pct, codex_weekly_src = _codex_weekly_pct()
    usage_details = _usage_details()
    andy_team_size = count_roster_agents()

    orchestrators = []
    for orch in ORCHESTRATORS:
        if orch["id"] == "root":
            team_size = andy_team_size
            team_label = "full roster ({} named agents)".format(team_size)
        else:
            team_size = len(ENGINE_TEAM_ROLES)
            team_label = "{} specialist roles (cloned team templates)".format(team_size)

        # Per-engine honest usage_detail (codex real / claude estimate / agy null).
        # Root is an orchestrator, not an engine: no detail of its own.
        detail = usage_details.get(orch["engine"]) if orch.get("engine") else None

        # Primary (5h) and weekly usage are distinct figures per orchestrator.
        if orch["id"] == "codex":
            usage_pct = detail.get("window_pct_primary") if detail else codex_primary_pct
            usage_source = codex_primary_src
            usage_pct_primary = usage_pct
            usage_pct_weekly = detail.get("window_pct_weekly") if detail else codex_weekly_pct
            usage_source_weekly = codex_weekly_src
        elif orch["id"] == "agy":
            usage_pct = None
            usage_source = "none (agy exposes no token/usage telemetry — only wall-clock duration)"
            usage_pct_primary = None
            usage_pct_weekly = None
            usage_source_weekly = "none (agy exposes no weekly usage telemetry)"
        elif orch["id"] == "claude":
            # No real quota ceiling exposed; tokens are an honest estimate (in detail).
            usage_pct = None
            usage_source = "none (claude has no local quota %; see usage_detail for estimated tokens)"
            usage_pct_primary = None
            usage_pct_weekly = None
            usage_source_weekly = "none (claude weekly quota % not exposed in local telemetry)"
        else:  # root
            usage_pct = None
            usage_source = "none (orchestrator; usage tracked per engine team)"
            usage_pct_primary = None
            usage_pct_weekly = None
            usage_source_weekly = "none (orchestrator; usage tracked per engine team)"

        entry = {
            "id": orch["id"],
            "name": orch["name"],
            "team_size": team_size,
            "team_label": team_label,
            "running_now": _running_now(entries, orch),
            "tasks_done_total": tasks_done[orch["id"]],
            "learning_loops_total": learning[orch["id"]],
            "usage_pct": usage_pct,                       # legacy: == primary
            "usage_source": usage_source,
            "usage_pct_primary": usage_pct_primary,       # 5h window
            "usage_pct_weekly": usage_pct_weekly,         # weekly window
            "usage_source_weekly": usage_source_weekly,
            # New honest per-engine detail: {tokens, source, confidence, note, ...}.
            "usage_detail": detail
            if detail is not None
            else {
                "tokens": None,
                "window_pct_primary": None,
                "window_pct_weekly": None,
                "source": "n/a (orchestrator, not an engine)",
                "confidence": "none",
                "note": "root is the orchestrator; usage is tracked per engine team",
            },
        }
        orchestrators.append(entry)

    return {
        "_meta": {
            "updated_at": now_iso(),
            "andy_team_size_source": str(ROSTER_FILE),
        },
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
