#!/usr/bin/env python3
"""
sub_orchestrator.py — model a real per-engine sub-orchestrator step.

Today Andy (the top orchestrator) reaches in and dispatches a single worker
directly via dispatch_worker.py. The per-engine teams (claude / agy / codex)
each own a cloned roster of specialists but never actually *orchestrate*.

This CLI gives each engine a sub-orchestrator that behaves like Andy:
  1. take a GOAL,
  2. decompose it into 2-5 concrete sub-tasks (deterministic, rule-based),
  3. for each sub-task run PTME to pick model + effort,
  4. assign the correct specialist role on THAT engine's team,
  5. enforce a worker != tester pairing (the worker role gets a *different*
     role as its tester),
  6. log each decision to logs/ptme_decisions.jsonl with an "orchestrator"
     field marking which sub-orchestrator owns it,
  7. set the chosen specialists active in agent_activity,
  8. print a plan summary.

stdlib-only. Reuses ptme, agent_activity, and dispatch_worker helpers rather
than duplicating their logic.

Usage:
    python scripts/sub_orchestrator.py plan --engine claude \
        --goal "Research the rate wall, design a watchdog, and document the playbook"

    # dry run (no log/activity writes), useful for QA:
    python scripts/sub_orchestrator.py plan --engine codex --goal "..." --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import agent_activity
import dispatch_worker
import ptme

ROOT = Path(__file__).resolve().parent.parent
PTME_LOG_FILE = ROOT / "logs" / "ptme_decisions.jsonl"
LESSONS_LOG_FILE = ROOT / "logs" / "orchestrator_lessons.jsonl"

VALID_ENGINES = ("claude", "agy", "codex")

# Map each engine to the friendly name of its sub-orchestrator.
ORCHESTRATOR_NAMES = {
    "claude": "Claude",
    "agy": "Antigravity",
    "codex": "Codex",
}

# worker role -> tester role. The tester is ALWAYS a different role so the
# worker != tester rule (team quality rubric #4) is structurally enforced.
TESTER_FOR_ROLE = {
    "coder": "qa",
    "web": "qa",
    "data": "qa",
    "designer": "qa",
    "content": "qa",
    "researcher": "qa",
    "qa": "security",        # if the work itself is QA, escalate the test to security
    "security": "qa",
    "orchestrator": "qa",
}

# Keyword -> specialist role routing for decomposed sub-tasks. First hit wins
# in the order listed inside _route_role().
ROLE_KEYWORDS = {
    "security": ("security", "secret", "auth", "vulnerab", "exploit", "harden", "attack surface"),
    "qa": ("qa", "test", "verify", "validate", "reproduce", "acceptance", "sign off", "sign-off"),
    "researcher": ("research", "investigate", "compare", "evaluate", "find out", "gather", "explore", "analyze"),
    "designer": ("design", "ui", "ux", "layout", "wireframe", "mockup", "brief", "component"),
    "data": ("schema", "migration", "query", "database", "sql", "table", "data model", "dataset"),
    "web": ("dashboard", "frontend", "page", "html", "css", "browser", "web ", "render", "feed"),
    "content": ("document", "write", "doc", "playbook", "readme", "memo", "copy", "draft", "post"),
    "coder": ("implement", "build", "code", "script", "refactor", "fix", "add", "create", "wire", "module"),
}

# Verbs that mark sub-task boundaries when we split a compound goal.
_SPLIT_VERBS = (
    "research", "investigate", "design", "build", "implement", "create",
    "add", "write", "document", "test", "verify", "validate", "review",
    "refactor", "fix", "wire", "migrate", "query", "render", "secure",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _route_role(sub_text: str) -> str:
    """Deterministically route a sub-task to a specialist role by keyword."""
    text = sub_text.lower()
    for role, keywords in ROLE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return role
    return "coder"  # safe default: most build work is coding


def _split_goal(goal: str) -> list[str]:
    """Rule-based decomposition of a goal into 2-5 concrete sub-tasks.

    Strategy (deterministic, no LLM call):
      1. Split on explicit separators: ';', ' and then ', ' then ', newlines.
      2. Within each chunk, split on ', ' boundaries that precede a known
         action verb (so "research X, design Y, document Z" -> 3 sub-tasks).
      3. Trim, drop empties, clamp to [2, 5]. If only one piece survives we
         synthesize an implementation + a verification sub-task so the
         worker != tester pairing always has something to test.
    """
    raw = (goal or "").strip()
    if not raw:
        return []

    # Stage 1: hard separators.
    parts: list[str] = []
    for chunk in re.split(r"\s*;\s*|\s+then\s+|\n+", raw):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)

    # Stage 2: verb-aware comma / "and" splitting within each chunk.
    expanded: list[str] = []
    verb_alt = "|".join(_SPLIT_VERBS)
    boundary = re.compile(r"\s*,\s+(?=(?:%s)\b)|\s+and\s+(?=(?:%s)\b)" % (verb_alt, verb_alt), re.IGNORECASE)
    for chunk in parts:
        sub = [s.strip(" ,.") for s in boundary.split(chunk) if s and s.strip(" ,.")]
        expanded.extend(sub if sub else [chunk])

    # Clean + dedupe while preserving order.
    seen = set()
    cleaned: list[str] = []
    for item in expanded:
        norm = item.strip(" ,.")
        if not norm:
            continue
        key = norm.lower()
        if key in seen:
            continue
        seen.add(key)
        # Capitalize first letter for readability.
        cleaned.append(norm[0].upper() + norm[1:] if norm else norm)

    # Stage 3: clamp to [2, 5].
    if not cleaned:
        cleaned = [raw]
    if len(cleaned) == 1:
        cleaned = [
            "Implement: " + cleaned[0],
            "Verify and QA: " + cleaned[0],
        ]
    return cleaned[:5]


def _make_sub_task_id(engine: str, base_id: str, index: int) -> str:
    return "{}-{}-S{}".format(engine.upper(), base_id, index)


def plan(engine: str, goal: str, base_id: str | None = None, dry_run: bool = False) -> dict:
    if engine not in VALID_ENGINES:
        raise ValueError("Unsupported engine '{}'".format(engine))

    sub_texts = _split_goal(goal)
    if not sub_texts:
        raise ValueError("Goal produced no sub-tasks")

    # Derive a stable base id from the goal if none was given.
    if not base_id:
        slug = re.sub(r"[^a-z0-9]+", "", (goal or "").lower())[:6] or "goal"
        base_id = slug.upper()

    sub_tasks: list[dict] = []
    for index, sub_text in enumerate(sub_texts, start=1):
        worker_role = _route_role(sub_text)
        tester_role = TESTER_FOR_ROLE.get(worker_role, "qa")
        if tester_role == worker_role:  # belt-and-suspenders: never self-test
            tester_role = "security" if worker_role != "security" else "qa"

        sub_task_id = _make_sub_task_id(engine, base_id, index)
        worker_id = "{}-{}".format(engine, worker_role)
        tester_id = "{}-{}".format(engine, tester_role)

        complexity = ptme.classify_complexity(sub_text)
        rec_model, rec_effort = ptme.recommend_for_complexity(complexity, family=engine)

        decision = None
        if not dry_run:
            # Log the PTME decision into the shared log, tagging the owner.
            decision = dispatch_worker.write_decision(
                task_id=sub_task_id,
                task_text=sub_text,
                engine=engine,
                worker_id=worker_id,
                role=worker_role,
                recommend_model=rec_model,
                recommend_effort=rec_effort,
                decided_by="{}_sub_orchestrator".format(engine),
            )
            dispatch_worker.annotate_ptme_decision(
                task_id=sub_task_id,
                ts=decision["ts"],
                updates={
                    "engine": engine,
                    "worker_id": worker_id,
                    "role": worker_role,
                    "orchestrator": engine,
                    "tester_id": tester_id,
                    "tester_role": tester_role,
                },
            )
            # Set the worker active for this engine's team.
            agent_activity.set(
                agent=worker_id,
                model=decision["decided_model"],
                effort=decision["decided_effort"],
                task=sub_text,
                status="running",
                reason="{} sub-orchestrator: {}".format(
                    ORCHESTRATOR_NAMES[engine], decision["reason"]
                ),
                task_id=sub_task_id,
            )
            dispatch_worker.annotate_activity_entry(
                worker_id, {"engine": engine, "role": worker_role}
            )

        sub_tasks.append(
            {
                "sub_task_id": sub_task_id,
                "text": sub_text,
                "complexity": complexity,
                "worker_role": worker_role,
                "worker_id": worker_id,
                "tester_role": tester_role,
                "tester_id": tester_id,
                "model": (decision["decided_model"] if decision else rec_model),
                "effort": (decision["decided_effort"] if decision else rec_effort),
                "decision_ref": ("{}@{}".format(sub_task_id, decision["ts"]) if decision else None),
            }
        )

    return {
        "orchestrator": engine,
        "orchestrator_name": ORCHESTRATOR_NAMES[engine],
        "goal": goal,
        "base_id": base_id,
        "dry_run": dry_run,
        "planned_at": now_iso(),
        "sub_tasks": sub_tasks,
    }


def record_lesson(orchestrator: str, lesson: str, task_id: str | None = None) -> dict:
    """Append a learning-loop entry attributed to a sub-orchestrator.

    orchestrator_stats.py counts these per-orchestrator for the dashboard's
    learning_loops_total. Keeping the schema small and append-only.
    """
    if orchestrator not in VALID_ENGINES and orchestrator != "andy":
        raise ValueError("Unsupported orchestrator '{}'".format(orchestrator))
    record = {
        "orchestrator": orchestrator,
        "task_id": task_id,
        "lesson": lesson,
        "ts": now_iso(),
    }
    LESSONS_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LESSONS_LOG_FILE.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _print_plan(result: dict) -> None:
    tag = " (dry-run)" if result.get("dry_run") else ""
    print("Sub-orchestrator: {} [{}]{}".format(
        result["orchestrator_name"], result["orchestrator"], tag))
    print("Goal: {}".format(result["goal"]))
    print("Decomposed into {} sub-task(s):".format(len(result["sub_tasks"])))
    for st in result["sub_tasks"]:
        print("  - [{}] {}".format(st["sub_task_id"], st["text"]))
        print("      complexity={}  model={} / {}".format(
            st["complexity"], st["model"], st["effort"]))
        print("      worker={}  ->  tester={}  (worker != tester enforced)".format(
            st["worker_id"], st["tester_id"]))


def cmd_plan(args: argparse.Namespace) -> int:
    result = plan(
        engine=args.engine,
        goal=args.goal,
        base_id=args.base_id,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_plan(result)
    return 0


def cmd_lesson(args: argparse.Namespace) -> int:
    record = record_lesson(args.orchestrator, args.lesson, task_id=args.task_id)
    print(json.dumps(record, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Per-engine sub-orchestrator: decompose, route, pair worker!=tester, log, activate."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Decompose a goal and assign this engine's specialists")
    p_plan.add_argument("--engine", required=True, choices=VALID_ENGINES)
    p_plan.add_argument("--goal", required=True)
    p_plan.add_argument("--base-id", help="Optional base id for sub-task ids (default: slug of goal)")
    p_plan.add_argument("--dry-run", action="store_true", help="Plan only; do not write logs or activity")
    p_plan.add_argument("--json", action="store_true", help="Emit the full plan as JSON")
    p_plan.set_defaults(func=cmd_plan)

    p_lesson = sub.add_parser("lesson", help="Record a learning-loop entry for an orchestrator")
    p_lesson.add_argument("--orchestrator", required=True)
    p_lesson.add_argument("--lesson", required=True)
    p_lesson.add_argument("--task-id")
    p_lesson.set_defaults(func=cmd_lesson)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
