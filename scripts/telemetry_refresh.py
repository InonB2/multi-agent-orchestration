#!/usr/bin/env python3
"""Clean and refresh dashboard/agent_activity.{json,js}.

  1. STALE LEGACY ROWS — purge non-canonical and ad-hoc duplicates.
  2. STALE "running" — any entry stuck "running" longer than EXPIRE_MIN (its dispatch likely died)
     is reverted to "idle" so the dashboard stops showing phantom work.
  3. NO REAL USAGE — inject codex's real window usage (from codex_usage.py) into the codex rows.
     claude/agy expose no programmatic usage today, so those stay honest-null.

Run at session start (and any time): python scripts/telemetry_refresh.py
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

import agent_activity as activity
import sidecar_lock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "dashboard")
JSON_PATH = os.path.join(DASH, "agent_activity.json")
JS_PATH = os.path.join(DASH, "agent_activity.js")
JS_PREFIX = "window.AGENT_ACTIVITY = "
EXPIRE_MIN = 45  # a "running" row older than this is treated as a dead dispatch -> idle

ENGINES = ("claude", "codex", "agy")
ROLES = ("orchestrator", "coder", "qa", "researcher", "designer", "security", "web", "content", "data")
CANONICAL = {f"{e}-{r}" for e in ENGINES for r in ROLES}

# Historical ad-hoc ids to drop entirely. Canonical ids are {engine}-{role}.
LEGACY = {"agy", "codex", "codex-2", "codex-3", "codex-dash", "agy-w1", "agy-w2", "codex-frontend"}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _age_min(ts):
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() / 60.0
    except Exception:
        return 1e9  # unparseable -> treat as ancient


def _codex_usage_pct():
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import codex_usage
        u = codex_usage.read_latest_usage()
        if isinstance(u, dict):
            return u.get("window_pct")
    except Exception:
        pass
    return None


def _usage_by_engine():
    """Per-engine REAL usage summary from dashboard/engine_usage.json so EVERY agent shows its
    engine's telemetry (not just codex). window_pct is % REMAINING (consistent with the panel);
    claude has no % so it carries token totals honestly."""
    try:
        with open(os.path.join(ROOT, "dashboard", "engine_usage.json"), encoding="utf-8") as f:
            eu = json.load(f)
    except Exception:
        eu = {}
    out = {}
    codex = eu.get("codex") or {}
    if codex.get("five_h_pct") is not None or codex.get("weekly_pct") is not None:
        out["codex"] = {"engine": "codex", "window_pct": codex.get("five_h_pct"),
                        "weekly_pct": codex.get("weekly_pct"), "kind": "remaining",
                        "source": codex.get("source") or "codex rollout"}
    agy = eu.get("agy") or {}
    gem, cg = (agy.get("gemini") or {}), (agy.get("claude_gpt") or {})
    if gem or cg:
        out["agy"] = {"engine": "agy", "window_pct": gem.get("weekly_pct"),
                      "weekly_pct": gem.get("weekly_pct"), "kind": "remaining",
                      "pools": {"gemini": gem.get("weekly_pct"), "claude_gpt": cg.get("weekly_pct")},
                      "source": agy.get("source") or "agy /usage"}
    claude = eu.get("claude") or {}
    if claude.get("tokens_7d") is not None or claude.get("pct") is not None:
        out["claude"] = {"engine": "claude", "window_pct": claude.get("pct"),
                         "tokens_7d": claude.get("tokens_7d"),
                         "kind": "tokens" if claude.get("pct") is None else "remaining",
                         "source": claude.get("source") or "logged tokens"}
    return out


def _activity_path() -> Path:
    return Path(JSON_PATH)


def _load_activity(path: Path | None = None) -> dict:
    return activity.read_activity(path or _activity_path())


def _save_activity(doc: dict, path: Path | None = None) -> None:
    activity.write_activity(doc, path or _activity_path())


def main():
    codex_pct = _codex_usage_pct()
    usage_by_engine = _usage_by_engine()

    def _apply_usage(entry, agent):
        eng = agent.split("-", 1)[0]
        u = usage_by_engine.get(eng)
        if u:
            entry["usage"] = dict(u)
            wp = u.get("window_pct")
            if isinstance(wp, (int, float)):
                entry["session_usage_pct"] = round(float(wp), 1)

    stats = {"kept": 0, "dropped": 0, "expired": 0}

    def mutate(doc: dict) -> dict:
        kept, dropped, expired = [], 0, 0
        seen = set()
        entries = doc.get("entries", [])
        for e in entries:
            agent = e.get("agent", "")
            if agent in LEGACY or agent not in CANONICAL:
                dropped += 1
                continue
            if agent in seen:        # de-dup, keep first (most recent already ordered by writer)
                continue
            seen.add(agent)
            # expire stale running
            if e.get("status") == "running" and _age_min(e.get("updated_at")) > EXPIRE_MIN:
                now_ts = _now()
                e["status"] = "idle"
                e["current_task"] = None
                e["task_id"] = None
                e["plan_id"] = None
                e["ended_at"] = now_ts
                e["duration_sec"] = None
                e["updated_at"] = now_ts
                expired += 1

            if e.get("started_at") and not e.get("spawned_at"):
                e["spawned_at"] = e.get("started_at")

            e.setdefault("spawned_at", None)
            e.setdefault("ended_at", None)
            e.setdefault("duration_sec", None)
            e.setdefault("plan_id", None)
            e.setdefault("planned_tokens", None)
            e.setdefault("actual_tokens", None)
            e.setdefault("qa_tester", None)
            e.setdefault("qa_verdict", None)
            e.setdefault("artifact_links", [])

            # inject real per-engine usage (codex %, agy pool %, claude tokens) for EVERY agent
            _apply_usage(e, agent)
            kept.append(e)

        # ensure every canonical agent exists (seed missing as idle, so the dash shows the full role grid)
        for agent in sorted(CANONICAL):
            if agent not in seen:
                seed = {
                    "agent": agent, "model": None, "effort": None, "current_task": None,
                    "status": "idle", "started_at": None, "spawned_at": None, "ended_at": None,
                    "duration_sec": None, "updated_at": _now(), "reason": "",
                    "session_usage_pct": 0, "task_id": None, "usage": None, "plan_id": None,
                    "planned_tokens": None, "actual_tokens": None, "qa_tester": None,
                    "qa_verdict": None, "artifact_links": [],
                }
                _apply_usage(seed, agent)
                kept.append(seed)

        doc["entries"] = kept
        doc.setdefault("_meta", {})
        doc["_meta"]["updated_at"] = _now()
        doc["_meta"]["generator"] = "scripts/telemetry_refresh.py"
        doc["_meta"]["version"] = "2.0.0"
        doc["_meta"]["note"] = ("Role-based ({engine}-{role}) live overlay. Per-engine usage is REAL from "
                                "engine_usage.json: codex/agy = % remaining, claude = rolling-7d tokens. "
                                f"Stale 'running' rows older than {EXPIRE_MIN}m auto-expire to idle.")
        stats["kept"] = len(kept)
        stats["dropped"] = dropped
        stats["expired"] = expired
        return doc

    sidecar_lock.locked_update(
        _activity_path(),
        mutate,
        load_fn=lambda locked_path: _load_activity(Path(locked_path)),
        save_fn=lambda doc, locked_path=None: _save_activity(
            doc,
            Path(locked_path) if locked_path else _activity_path(),
        ),
    )
    print(
        f"telemetry refreshed: kept {stats['kept']} "
        f"(dropped {stats['dropped']} legacy/ad-hoc, expired {stats['expired']} stale-running), "
        f"codex usage={codex_pct if codex_pct is not None else 'n/a'}%"
    )

    _refresh_secondary_feeds()


def _refresh_secondary_feeds():
    """Regenerate the two feeds that used to silently stale (root cause of the
    8-day-old orchestrator_stats + ghost 'running' rows found 2026-07-02). Wrapped
    so a failure here can never block session init."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    # 1) orchestrator_stats.{json,js} — fresh, real per-engine usage.
    try:
        import orchestrator_stats
        orchestrator_stats.write_stats(orchestrator_stats.build_stats())
        print("  orchestrator_stats refreshed")
    except Exception as exc:
        print(f"  orchestrator_stats refresh skipped: {exc}")
    # 2) live_tasks.{json,js} — expire stale 'running' rows, rewrite both mirrors.
    #    Done under the same sidecar lock the live dispatch paths (start/complete)
    #    use, so a concurrent dispatch can't lose an update to this reconcile.
    try:
        import dispatch_worker
        import sidecar_lock
        counter = {"n": 0}

        def _mutate(lt):
            for row in lt.get("entries", []):
                if (
                    row.get("status") == "running"
                    and _age_min(row.get("updated_at") or row.get("started_at")) > EXPIRE_MIN
                ):
                    now_ts = _now()
                    row["status"] = "expired"
                    row["expired_reason"] = f"stale >{EXPIRE_MIN}m at session-init reconcile"
                    row["ended_at"] = row.get("ended_at") or row.get("finished_at") or now_ts
                    row["finished_at"] = row.get("finished_at") or row["ended_at"]
                    row["completed_at"] = row.get("completed_at") or row["ended_at"]
                    row["updated_at"] = now_ts
                    if row.get("spawned_at") is None:
                        row["spawned_at"] = row.get("started_at")
                    if row.get("duration_sec") is None:
                        st = row.get("started_at") or row.get("spawned_at")
                        row["duration_sec"] = dispatch_worker.duration_seconds(st, row["ended_at"]) if st else None
                        row["duration_seconds"] = row["duration_sec"]
                    counter["n"] += 1
            meta = lt.setdefault("_meta", {})
            meta["updated_at"] = _now()
            meta["generator"] = "scripts/telemetry_refresh.py"
            meta["version"] = "2.0.0"
            return lt

        sidecar_lock.locked_update(
            dispatch_worker.LIVE_TASKS_FILE, _mutate,
            load_fn=dispatch_worker.read_live_tasks,
            save_fn=dispatch_worker.write_live_tasks,
        )
        print(f"  live_tasks refreshed (expired {counter['n']} stale-running)")
    except Exception as exc:
        print(f"  live_tasks refresh skipped: {exc}")


if __name__ == "__main__":
    main()
