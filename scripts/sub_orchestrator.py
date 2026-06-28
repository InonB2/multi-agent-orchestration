#!/usr/bin/env python3
"""
sub_orchestrator.py — model a real per-engine sub-orchestrator step.

Today Root (the top orchestrator) reaches in and dispatches a single worker
directly via dispatch_worker.py. The per-engine teams (claude / agy / codex)
each own a cloned roster of specialists but never actually *orchestrate*.

This CLI gives each engine a sub-orchestrator that behaves like Root:
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

try:  # Phase 4 semantic router — optional, guarded.
    import router as semantic_router
except Exception:  # pragma: no cover - defensive
    semantic_router = None  # type: ignore[assignment]

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


def resolve_engine(engine: str, goal: str) -> tuple[str, dict | None]:
    """Resolve which engine orchestrates a goal.

    engine == "auto" consults the semantic router (capability + rate-wall
    failover + load balancing) to pick the engine. An explicit engine is
    honored unchanged (backward compatible). Falls back to 'claude' if the
    router is unavailable.
    """
    if engine != "auto":
        return engine, None
    if semantic_router is None:
        return "claude", None
    result = semantic_router.route(task_text=goal)
    return result["engine"], result


def plan(engine: str, goal: str, base_id: str | None = None, dry_run: bool = False) -> dict:
    engine, route_result = resolve_engine(engine, goal)
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

        # Stage the two-tier QA structure for this sub-task (verdicts pending
        # until the actual runs land). TIER 1 internal same-engine (worker !=
        # tester), TIER 2 external different-engine QA + security.
        qa_plan = two_tier_qa(
            engine=engine,
            worker_role=worker_role,
            sub_task_id=sub_task_id,
            dry_run=dry_run,
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
                "internal_qa": qa_plan["internal_qa"],
                "external_qa": qa_plan["external_qa"],
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
        "routed_via": (route_result.get("chosen_via") if route_result else "explicit engine"),
        "router_explanation": (route_result.get("explanation") if route_result else None),
        "sub_tasks": sub_tasks,
    }


# ---------------------------------------------------------------------------
# Two-tier QA (owner's explicit ask)
# ---------------------------------------------------------------------------
# Map each engine to the OTHER engines that can host an external QA/security
# gate. External QA must be a DIFFERENT engine than the worker's engine so a
# team never grades its own homework at the external tier.
_EXTERNAL_ENGINE_ORDER = ("claude", "codex", "agy")


def _internal_tester_role(worker_role: str) -> str:
    """Pick an INTERNAL same-engine tester role that differs from the worker."""
    tester = TESTER_FOR_ROLE.get(worker_role, "qa")
    if tester == worker_role:  # never self-test
        tester = "security" if worker_role != "security" else "qa"
    return tester


def _external_engine_for(worker_engine: str, prefer: str | None = None) -> str:
    """Pick an external engine (different from the worker's) for the outer gate."""
    if prefer and prefer in VALID_ENGINES and prefer != worker_engine:
        return prefer
    for eng in _EXTERNAL_ENGINE_ORDER:
        if eng != worker_engine:
            return eng
    return worker_engine  # single-engine degenerate fallback (shouldn't happen)


def two_tier_qa(
    engine: str,
    worker_role: str,
    sub_task_id: str,
    internal_verdict: str | None = None,
    external_verdict: str | None = None,
    external_engine: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Stage a two-tier QA on a completed worker sub-task.

    TIER 1 — INTERNAL: a different role on the SAME engine team checks the work
             (worker != tester, same engine). Must PASS before tier 2.
    TIER 2 — EXTERNAL: a different ENGINE's QA + a security test runs the outer
             gate (different-engine review + security).

    Verdicts can be supplied (when the run already happened) or left None
    (staged/pending). The structure is recorded on the ptme decision record as
    internal_qa{tester, engine, role, verdict} and external_qa{tester, engine,
    role, verdict, security{tester, verdict}}. Returns that structure.

    Worker != tester is asserted at BOTH tiers:
      * internal tester role != worker role (same engine).
      * external tester engine != worker engine.
    """
    if engine not in VALID_ENGINES:
        raise ValueError("Unsupported engine '{}'".format(engine))

    internal_role = _internal_tester_role(worker_role)
    assert internal_role != worker_role, "internal worker==tester (role)"
    internal_id = "{}-{}".format(engine, internal_role)

    ext_engine = _external_engine_for(engine, prefer=external_engine)
    assert ext_engine != engine, "external worker==tester (engine)"
    external_id = "{}-qa".format(ext_engine)
    security_id = "{}-security".format(ext_engine)

    def _norm(v: str | None) -> str | None:
        if v is None:
            return None
        t = str(v).strip().lower()
        if t in ("pass", "passed", "ok", "green", "go"):
            return "pass"
        if t in ("fail", "failed", "red", "no-go", "nogo", "reject", "rejected"):
            return "fail"
        return t or None

    iv = _norm(internal_verdict)
    # The external tier only runs once internal PASSES (owner's ordering).
    external_gated = iv == "pass"
    ev = _norm(external_verdict) if external_gated else None

    internal_qa = {
        "tier": "internal",
        "engine": engine,
        "tester_id": internal_id,
        "tester_role": internal_role,
        "verdict": iv,  # None = staged/pending
    }
    external_qa = {
        "tier": "external",
        "engine": ext_engine,
        "tester_id": external_id,
        "tester_role": "qa",
        "verdict": ev,  # None until internal passes AND it runs
        "gated_on_internal_pass": external_gated,
        "security": {
            "tester_id": security_id,
            "tester_role": "security",
            "verdict": (_norm(external_verdict) if external_gated else None),
        },
    }

    record = {
        "sub_task_id": sub_task_id,
        "worker_role": worker_role,
        "worker_id": "{}-{}".format(engine, worker_role),
        "internal_qa": internal_qa,
        "external_qa": external_qa,
        "ts": now_iso(),
    }

    if not dry_run:
        dispatch_worker._annotate_latest_ptme_for_task(
            sub_task_id,
            {"internal_qa": internal_qa, "external_qa": external_qa},
        )
    return record


def cmd_qa(args: argparse.Namespace) -> int:
    record = two_tier_qa(
        engine=args.engine,
        worker_role=args.worker_role,
        sub_task_id=args.sub_task_id,
        internal_verdict=args.internal_verdict,
        external_verdict=args.external_verdict,
        external_engine=args.external_engine,
        dry_run=args.dry_run,
    )
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


def record_lesson(orchestrator: str, lesson: str, task_id: str | None = None) -> dict:
    """Append a learning-loop entry attributed to a sub-orchestrator.

    orchestrator_stats.py counts these per-orchestrator for the dashboard's
    learning_loops_total. Keeping the schema small and append-only.
    """
    if orchestrator not in VALID_ENGINES and orchestrator != "root":
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
    p_plan.add_argument(
        "--engine",
        required=True,
        choices=VALID_ENGINES + ("auto",),
        help="Orchestrating engine, or 'auto' to let the semantic router pick.",
    )
    p_plan.add_argument("--goal", required=True)
    p_plan.add_argument("--base-id", help="Optional base id for sub-task ids (default: slug of goal)")
    p_plan.add_argument("--dry-run", action="store_true", help="Plan only; do not write logs or activity")
    p_plan.add_argument("--json", action="store_true", help="Emit the full plan as JSON")
    p_plan.set_defaults(func=cmd_plan)

    p_qa = sub.add_parser("qa", help="Stage/record two-tier QA (internal then external) on a sub-task")
    p_qa.add_argument("--engine", required=True, choices=VALID_ENGINES)
    p_qa.add_argument("--worker-role", required=True, choices=dispatch_worker.VALID_ROLES)
    p_qa.add_argument("--sub-task-id", required=True)
    p_qa.add_argument("--internal-verdict", help="pass/fail from the internal same-engine tester")
    p_qa.add_argument("--external-verdict", help="pass/fail from the external different-engine QA+security")
    p_qa.add_argument("--external-engine", choices=VALID_ENGINES, help="Preferred external engine (must differ from --engine)")
    p_qa.add_argument("--dry-run", action="store_true")
    p_qa.set_defaults(func=cmd_qa)

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
