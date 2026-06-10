#!/usr/bin/env python3
"""
task_router.py — Route tasks to the best provider based on keyword matching.

Reads tasks/active_tasks.json, scores each task's title + notes against
keyword lists for each provider, and assigns preferred_provider.

Usage:
    python scripts/task_router.py                      # updates active_tasks.json
    python scripts/task_router.py --dry-run            # preview only
    python scripts/task_router.py --task-id TASK-001   # route a single task by ID
"""

import json
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# TOML import — stdlib (Python 3.11+) with fallback to tomli
# ---------------------------------------------------------------------------
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

ROOT       = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "tasks" / "active_tasks.json"
_CONFIG_DIR = ROOT / "config" / "agents"

ROUTING_RULES = {
    "codex": [
        "implement", "refactor", "pr", "pull request", "api", "endpoint",
        "migration", "schema", "fix", "bug", "patch", "unit test", "ci",
        "build", "compile", "typescript", "python script", "security audit",
        "adversarial review", "code review", "lint",
    ],
    "antigravity": [
        "research", "summarize", "analyze", "plan", "design", "ui", "ux",
        "browser", "visual", "screenshot", "e2e", "end-to-end", "artifact",
        "document", "report", "comparison", "evaluation", "benchmark",
        "scraping", "scrape", "web scraping",
    ],
    "claude-code": [
        "orchestrate", "delegate", "architect", "coordinate", "multi-file",
        "debug", "trace", "subagent", "workflow",
    ],
}

DEFAULT_PROVIDER = "claude-code"


# ---------------------------------------------------------------------------
# File-lock helpers (cross-platform sidecar-file pattern)  [REL-2]
# ---------------------------------------------------------------------------

def _acquire_lock(lock_path: Path, timeout: int = 10) -> bool:
    """Try to create *lock_path* exclusively. Returns True on success, False on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.05)
    return False


def _release_lock(lock_path: Path) -> None:
    """Delete the sidecar lock file, ignoring missing-file errors."""
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Provider-type lookup (for enriched routing output)
# ---------------------------------------------------------------------------

def _load_toml_safe(path: Path, agent_name: str = "") -> dict:
    """Load a TOML file; return {} on any error (never raises).

    REL-4: warns on parse error so misconfigured agent configs are surfaced.
    """
    if tomllib is None or not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        if agent_name:
            print(
                "[WARN] Could not parse TOML for agent '{}': {}".format(agent_name, exc),
                file=sys.stderr,
            )
        return {}


def _get_provider_info(agent_name: str) -> tuple:
    """Return (provider_type, model_id) for *agent_name*. Defaults to ('cli', None)."""
    try:
        defaults  = _load_toml_safe(_CONFIG_DIR / "_defaults.toml")
        agent_cfg = _load_toml_safe(
            _CONFIG_DIR / "{}.toml".format(agent_name.lower()),
            agent_name,
        )
        # Simple merge: agent overrides defaults for provider section
        merged_provider = dict(defaults.get("provider", {}))
        merged_provider.update(agent_cfg.get("provider", {}))
        ptype    = merged_provider.get("type", "cli")
        model_id = merged_provider.get("model_id")
        return ptype, model_id
    except Exception:
        return "cli", None


def score_task(task: dict) -> dict:
    """Score a task's title + notes against each provider's keyword list.

    EDGE-1: Uses word-boundary regex instead of plain substring matching to
    prevent false positives like "prefix" matching "fix" or "rapid" matching "api".
    """
    text = " ".join([
        task.get("title", ""),
        task.get("notes", "") or "",
    ])

    scores = {provider: 0 for provider in ROUTING_RULES}
    for provider, keywords in ROUTING_RULES.items():
        for kw in keywords:
            # Word-boundary match: re.IGNORECASE handles capitalisation
            if re.search(r'\b' + re.escape(kw) + r'\b', text, re.IGNORECASE):
                scores[provider] += 1
    return scores


def pick_provider(scores: dict) -> str:
    """Return the provider with the highest score.

    On equal scores, provider priority is: codex > antigravity > claude-code.
    Falls back to the default provider (claude-code) when all scores are zero.
    """
    best_score = max(scores.values())
    if best_score == 0:
        return DEFAULT_PROVIDER
    # Pick deterministically on tie using explicit priority order
    priority_order = ["codex", "antigravity", "claude-code"]
    for provider in priority_order:
        if scores.get(provider, 0) == best_score:
            return provider
    return DEFAULT_PROVIDER


def route_tasks(dry_run=False, task_id_filter=None):
    # Load tasks (no lock needed for dry-run; lock acquired before write)
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("[ERROR] tasks/active_tasks.json not found at {}".format(TASKS_FILE), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print("[ERROR] Failed to parse tasks/active_tasks.json: {}".format(exc), file=sys.stderr)
        sys.exit(1)

    all_tasks = data.get("tasks", [])

    # If a specific task ID was requested, filter to just that task
    if task_id_filter:
        tasks = [t for t in all_tasks if t.get("task_id") == task_id_filter]
        if not tasks:
            print("[ERROR] Task '{}' not found in active_tasks.json.".format(task_id_filter),
                  file=sys.stderr)
            sys.exit(1)
    else:
        tasks = all_tasks

    counters = {provider: 0 for provider in ROUTING_RULES}
    routed_count = 0

    for task in tasks:
        if "preferred_provider" in task:
            # EDGE-2: inform the operator instead of silently skipping
            tid = task.get("task_id", "?")
            print("[INFO] Task '{}' already has preferred_provider='{}' — skipping.".format(
                tid, task["preferred_provider"]
            ))
            continue

        scores = score_task(task)
        provider = pick_provider(scores)

        task_id = task.get("task_id", "?")
        title   = task.get("title", "(no title)")
        score_summary = ", ".join("{}={}".format(p, s) for p, s in scores.items())

        # Look up provider type for enriched output
        ptype, model_id = _get_provider_info(provider)
        if ptype == "api" and model_id:
            provider_label = "[provider: api | model: {}]".format(model_id)
        else:
            provider_label = "[provider: {}]".format(ptype)

        # Strip non-ASCII characters to avoid cp1252 encoding errors on Windows console
        safe_title = title[:60].encode("ascii", errors="replace").decode("ascii")
        print("  {}: '{}' -> {}  {}  (scores: {})".format(
            task_id, safe_title, provider, provider_label, score_summary
        ))

        if not dry_run:
            task["preferred_provider"] = provider

        counters[provider] += 1
        routed_count += 1

    if dry_run:
        summary_parts = ["{} -> {}".format(n, p) for p, n in counters.items()]
        print("\n[DRY-RUN] Would route {} tasks: {}".format(
            routed_count, ", ".join(summary_parts)
        ))
        return

    # EDGE-2: skip the file write entirely if nothing changed
    if routed_count == 0:
        print("\nRouted 0 tasks (nothing to update).")
        return

    # REL-2: acquire lock before writing to guard against concurrent processes
    lock_path = Path(str(TASKS_FILE) + ".lock")
    if not _acquire_lock(lock_path):
        print("[ERROR] Could not acquire lock on tasks file", file=sys.stderr)
        sys.exit(1)
    try:
        # Atomic write — prevents file corruption on interrupted write (MINOR-2)
        tmp = TASKS_FILE.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, TASKS_FILE)
    finally:
        _release_lock(lock_path)

    summary_parts = ["{} -> {}".format(n, p) for p, n in counters.items()]
    print("\nRouted {} tasks: {}".format(routed_count, ", ".join(summary_parts)))
    print("[OK] Updated {}".format(TASKS_FILE))


def main():
    argv = sys.argv[1:]
    dry_run = "--dry-run" in argv

    # Extract --task-id value if present
    task_id_filter = None
    if "--task-id" in argv:
        idx = argv.index("--task-id")
        if idx + 1 < len(argv):
            task_id_filter = argv[idx + 1]
        else:
            print("[ERROR] --task-id requires a value, e.g. --task-id TASK-001", file=sys.stderr)
            sys.exit(1)

    if dry_run:
        print("[DRY-RUN] Routing preview (no changes written):\n")
    elif task_id_filter:
        print("Routing task '{}' (will write to active_tasks.json):\n".format(task_id_filter))
    else:
        print("Routing tasks (will write to active_tasks.json):\n")

    route_tasks(dry_run=dry_run, task_id_filter=task_id_filter)


if __name__ == "__main__":
    main()
