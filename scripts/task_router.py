#!/usr/bin/env python3
"""
task_router.py — Route tasks to the best provider based on keyword matching.

Reads tasks/active_tasks.json, scores each task's title + notes against
keyword lists for each provider, and assigns preferred_provider.

Usage:
    python scripts/task_router.py           # updates active_tasks.json
    python scripts/task_router.py --dry-run # preview only
"""

import json
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


def route_tasks(dry_run=False):
    # Load tasks
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("[ERROR] tasks/active_tasks.json not found at {}".format(TASKS_FILE), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print("[ERROR] Failed to parse tasks/active_tasks.json: {}".format(exc), file=sys.stderr)
        sys.exit(1)

    tasks = data.get("tasks", [])
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

    # Write back
    TASKS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    summary_parts = ["{} -> {}".format(n, p) for p, n in counters.items()]
    print("\nRouted {} tasks: {}".format(routed_count, ", ".join(summary_parts)))
    print("[OK] Updated {}".format(TASKS_FILE))


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("[DRY-RUN] Routing preview (no changes written):\n")
    else:
        print("Routing tasks (will write to active_tasks.json):\n")

    route_tasks(dry_run=dry_run)


if __name__ == "__main__":
    main()
