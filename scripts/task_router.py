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
import sys
from pathlib import Path

ROOT       = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "tasks" / "active_tasks.json"

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


def score_task(task: dict) -> dict:
    """Score a task's title + notes against each provider's keyword list."""
    text = " ".join([
        task.get("title", ""),
        task.get("notes", "") or "",
    ]).lower()

    scores = {provider: 0 for provider in ROUTING_RULES}
    for provider, keywords in ROUTING_RULES.items():
        for kw in keywords:
            if kw.lower() in text:
                scores[provider] += 1
    return scores


def pick_provider(scores: dict) -> str:
    """Return the provider with the highest score; default to claude-code on tie."""
    best_score = max(scores.values())
    if best_score == 0:
        return DEFAULT_PROVIDER
    # Pick deterministically: prefer codex > antigravity > claude-code on equal scores
    priority_order = ["codex", "antigravity", "claude-code"]
    for provider in priority_order:
        if scores.get(provider, 0) == best_score:
            return provider
    return DEFAULT_PROVIDER


def route_tasks(dry_run=False, task_id_filter=None):
    # Load tasks
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
            # Already has a provider — skip
            continue

        scores = score_task(task)
        provider = pick_provider(scores)

        task_id = task.get("task_id", "?")
        title   = task.get("title", "(no title)")
        score_summary = ", ".join("{}={}".format(p, s) for p, s in scores.items())

        # Strip non-ASCII characters to avoid cp1252 encoding errors on Windows console
        safe_title = title[:60].encode("ascii", errors="replace").decode("ascii")
        print("  {}: '{}' -> {}  (scores: {})".format(
            task_id, safe_title, provider, score_summary
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

    # Atomic write — prevents file corruption on interrupted write (MINOR-2)
    tmp = TASKS_FILE.with_suffix('.tmp')
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, TASKS_FILE)

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
