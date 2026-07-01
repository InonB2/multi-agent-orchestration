#!/usr/bin/env python3
"""
router.py — semantic capability router with rate-wall failover + load balancing.

Today routing is keyword-only (scripts/task_router.py) and the engine is hand-
picked. This module adds the Phase-4 INTELLIGENCE LAYER on top of the rebuilt
engine:

  * a CAPABILITY PROFILE per engine+role (skills/strengths sourced from ptme),
  * route(task_text) that scores each AVAILABLE engine by capability match to
    the task (ptme complexity + signal extraction + a deterministic capability
    score, structured so a real embedding model can drop in later),
  * RATE-WALL FAILOVER: a walled engine (per rate_wall_watchdog) is EXCLUDED and
    the task is rerouted to the next most-capable AVAILABLE engine — by
    capability, not keyword,
  * LOAD BALANCING: each engine's weekly usage_pct and current running_now count
    pull its score down, so work spreads across Claude+agy when codex is down
    without burning any single engine's quota fast. Tunable via RouterConfig.

An explicit engine override always wins (backward compatibility with the
existing --engine flag). The chosen result is engine-scoped: the model is always
from the chosen engine's own family (delegated to ptme), never a foreign one.

stdlib-only. No secrets, no subprocess, no network.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import ptme
import role_scoring
import routing_table

try:  # optional: promoted rules consultation (guarded — empty/missing = no-op)
    import learning_loop
except Exception:  # pragma: no cover - defensive
    learning_loop = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent

VALID_ENGINES = ("claude", "codex", "agy")

# ---------------------------------------------------------------------------
# Capability profiles (TUNABLE). Each engine+role advertises capability keywords;
# a task's signal/keyword overlap with these drives the capability score. The
# scorer is deterministic now but isolated behind capability_score() so a real
# embedding similarity can replace the keyword overlap later without touching
# route().
# ---------------------------------------------------------------------------
ENGINE_CAPABILITIES = {
    "claude": {
        "strengths": [
            "architecture", "security", "orchestration", "design", "reasoning",
            "coordination", "documentation", "audit", "judgment", "multi-file",
            "refactor", "pipeline",
        ],
        "preferred_roles": ("orchestrator", "security", "designer", "content", "coder", "web"),
    },
    "codex": {
        "strengths": [
            "coding", "implement", "refactor", "bugfix", "terminal", "script",
            "single-file", "multi-file", "patch", "test", "ci", "build",
            "migration", "schema",
        ],
        "preferred_roles": ("coder", "data", "qa", "security"),
    },
    "agy": {
        "strengths": [
            "research", "investigate", "compare", "evaluate", "draft", "summarize",
            "synthesis", "parallel", "broad", "planning", "explore", "report",
            "benchmark",
        ],
        "preferred_roles": ("researcher", "content", "designer"),
    },
}

# Role inference now lives in role_scoring (scored, verb-precedence; shared with
# sub_orchestrator so the two can never drift). ROLE_KEYWORDS is retained as a
# deprecated alias of the secondary-signal vocabulary for backward compatibility
# with any caller that imported it; infer_role() no longer reads it.
ROLE_KEYWORDS = role_scoring.SECONDARY_SIGNALS


@dataclass
class RouterConfig:
    """Tunable knobs for capability vs load trade-off (owner requirement)."""
    weekly_usage_penalty: float = 3.0     # score lost per 1.0 (=100%) weekly usage
    running_now_penalty: float = 0.5      # score lost per concurrently running worker
    near_wall_pct: float = 90.0           # weekly usage at/above this = avoid hard
    near_wall_penalty: float = 5.0        # extra penalty once near the wall
    capability_weight: float = 1.0        # multiplier on the raw capability match
    advisory_bonus: float = 0.35          # tie-break nudge for role-advised engine
    rules_path: Path | None = None        # promoted_rules.json (None = default)


# ---------------------------------------------------------------------------
# Capability scoring (deterministic; embedding-ready seam).
# ---------------------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    return [t for t in "".join(c if c.isalnum() else " " for c in (text or "").lower()).split() if t]


def capability_score(task_text: str, engine: str) -> tuple[float, list[str]]:
    """Deterministic capability match between a task and an engine.

    Returns (score, matched_terms). Replace the body with embedding cosine
    similarity later; route() only depends on this signature.
    """
    profile = ENGINE_CAPABILITIES.get(engine)
    if not profile:
        return 0.0, []
    task_tokens = set(_tokens(task_text))
    matched = []
    score = 0.0
    for strength in profile["strengths"]:
        # phrase or token overlap
        if " " in strength:
            if strength in (task_text or "").lower():
                matched.append(strength)
                score += 1.0
        elif strength in task_tokens:
            matched.append(strength)
            score += 1.0
    return score, matched


def infer_role(task_text: str) -> str:
    text = (task_text or "").lower()
    for role, kws in ROLE_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return role
    return "coder"


# ---------------------------------------------------------------------------
# Availability + load (rate-wall + live usage).
# ---------------------------------------------------------------------------
def engine_available(engine: str) -> tuple[bool, str | None]:
    """Return (available, reason_if_walled). Imports watchdog lazily so the
    module loads even if telemetry deps are unusual.

    The watchdog's should-dispatch prints to stdout; we suppress that so the
    router's own JSON output is not polluted, and read its exit code only.
    """
    try:
        import contextlib
        import io
        import rate_wall_watchdog
    except Exception:
        return True, None
    try:
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            rc = rate_wall_watchdog.cmd_should_dispatch(
                argparse.Namespace(engine=engine)
            )
    except SystemExit as exc:  # defensive
        rc = int(exc.code or 0)
    except Exception:
        return True, None
    if rc == 0:
        return True, None
    return False, "rate-walled (rate_wall_watchdog should-dispatch exit {})".format(rc)


def engine_load(engine: str) -> dict:
    """Return {'weekly_pct': float|None, 'running_now': int} for an engine.

    Reads the same live signals the dashboard uses (orchestrator_stats). Fully
    guarded: any failure yields neutral load so routing never crashes.
    """
    weekly_pct = None
    running_now = 0
    try:
        import orchestrator_stats as os_stats
        import agent_activity

        if engine == "codex":
            weekly_pct, _src = os_stats._codex_weekly_pct()
        activity = agent_activity.read_activity(agent_activity.ACTIVITY_FILE)
        entries = activity.get("entries", [])
        orch = {"id": engine}
        running_now = os_stats._running_now(entries, orch)
    except Exception:
        pass
    return {"weekly_pct": weekly_pct, "running_now": running_now}


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------
def _score_engine(
    task_text: str,
    engine: str,
    config: RouterConfig,
    role: str | None = None,
    load: dict | None = None,
) -> dict:
    cap, matched = capability_score(task_text, engine)
    cap *= config.capability_weight
    load = load if load is not None else engine_load(engine)
    weekly = load.get("weekly_pct")
    running = int(load.get("running_now") or 0)

    penalty = 0.0
    reasons = []
    if matched:
        reasons.append("capability match: {}".format(", ".join(matched)))
    else:
        reasons.append("no direct capability match")
    if weekly is not None:
        frac = float(weekly) / 100.0
        load_pen = frac * config.weekly_usage_penalty
        penalty += load_pen
        reasons.append("weekly usage {:.0f}% (-{:.2f})".format(weekly, load_pen))
        if weekly >= config.near_wall_pct:
            penalty += config.near_wall_penalty
            reasons.append("near wall >= {:.0f}% (-{:.2f})".format(config.near_wall_pct, config.near_wall_penalty))
    if running:
        run_pen = running * config.running_now_penalty
        penalty += run_pen
        reasons.append("{} running now (-{:.2f})".format(running, run_pen))

    advisory_engine = routing_table.advisory_engine_for_role(role)
    bonus = config.advisory_bonus if advisory_engine == engine else 0.0
    if bonus:
        reasons.append("role advisory {} (+{:.2f})".format(role, bonus))

    final = cap - penalty + bonus
    return {
        "engine": engine,
        "capability_score": round(cap, 4),
        "matched": matched,
        "load_penalty": round(penalty, 4),
        "advisory_bonus": round(bonus, 4),
        "final_score": round(final, 4),
        "weekly_pct": weekly,
        "running_now": running,
        "reasons": reasons,
    }


def route(
    task_text: str,
    candidate_engines: list[str] | None = None,
    override_engine: str | None = None,
    role: str | None = None,
    config: RouterConfig | None = None,
) -> dict:
    """Choose engine+role+model(engine-scoped)+effort WITH an explanation.

    Order of operations:
      1. explicit override_engine (if valid) WINS — capability/load ignored,
         backward-compatible with the existing --engine flag.
      2. otherwise score every AVAILABLE candidate engine by capability minus
         load; exclude rate-walled engines and record the failover reason.
      3. resolve role (inferred if not given), then ask ptme for the engine-
         scoped model+effort. The model is ALWAYS from the chosen engine family.
      4. consult promoted learning rules (guarded) and surface any matching ones.
    """
    config = config or RouterConfig()
    candidates = [e for e in (candidate_engines or VALID_ENGINES) if e in VALID_ENGINES]
    if not candidates:
        candidates = list(VALID_ENGINES)

    resolved_role = (role or infer_role(task_text))
    complexity = ptme.classify_complexity(task_text)
    failover_reasons: list[str] = []

    # --- 1) explicit override wins -------------------------------------------
    if override_engine:
        ov = override_engine.lower()
        if ov in VALID_ENGINES:
            model, effort = ptme.recommend_for_complexity(complexity, family=ov)
            return _finalize(
                task_text, ov, resolved_role, complexity, model, effort,
                chosen_via="explicit override", scores=[], excluded=[],
                failover_reasons=["explicit --engine override: {}".format(ov)],
                config=config,
            )

    # --- 2) availability filter (rate-wall failover) -------------------------
    available = []
    excluded = []
    for engine in candidates:
        ok, reason = engine_available(engine)
        if ok:
            available.append(engine)
        else:
            excluded.append({"engine": engine, "reason": reason})
            failover_reasons.append("excluded {}: {}".format(engine, reason))

    if not available:
        # Everything walled — fail open to the most-capable candidate by
        # capability alone so we never deadlock; flag it loudly.
        scored = sorted(
            (
                _score_engine(
                    task_text,
                    e,
                    config,
                    role=resolved_role,
                    load={"weekly_pct": None, "running_now": 0},
                )
                for e in candidates
            ),
            key=lambda s: s["final_score"],
            reverse=True,
        )
        chosen = scored[0]["engine"]
        failover_reasons.append("ALL candidates walled — failing open to most-capable {}".format(chosen))
        model, effort = ptme.recommend_for_complexity(complexity, family=chosen)
        return _finalize(
            task_text, chosen, resolved_role, complexity, model, effort,
            chosen_via="fail-open (all walled)", scores=scored, excluded=excluded,
            failover_reasons=failover_reasons, config=config,
        )

    # --- 3) score available engines by capability minus load -----------------
    scored = sorted(
        (_score_engine(task_text, e, config, role=resolved_role) for e in available),
        key=lambda s: (s["final_score"], s["capability_score"]),
        reverse=True,
    )
    chosen = scored[0]["engine"]
    if excluded:
        failover_reasons.append("rerouted by capability to {} (next most-capable available)".format(chosen))

    model, effort = ptme.recommend_for_complexity(complexity, family=chosen)
    return _finalize(
        task_text, chosen, resolved_role, complexity, model, effort,
        chosen_via="capability+load score", scores=scored, excluded=excluded,
        failover_reasons=failover_reasons, config=config,
    )


def _finalize(
    task_text, engine, role, complexity, model, effort,
    chosen_via, scores, excluded, failover_reasons, config,
) -> dict:
    name, specialization = ptme.specialist_for_role(role)
    advisory_engine = routing_table.advisory_engine_for_role(role)
    if model and not ptme.engine_allows_model(engine, model):  # pragma: no cover - defensive
        model, effort = ptme.recommend_for_complexity(complexity, family=engine)

    # Guarded learning-rule consultation.
    applied_rules = []
    if learning_loop is not None:
        try:
            ctx = {
                "complexity": complexity,
                "engine": engine,
                "role": role,
                "signals": [s for s in ptme.SIMPLE_SIGNALS if s in (task_text or "").lower()],
            }
            for rule in learning_loop.consult(ctx, path=config.rules_path):
                applied_rules.append(rule.get("rule"))
        except Exception:
            applied_rules = []

    return {
        "engine": engine,
        "role": role,
        "assigned_name": name,
        "specialization": specialization,
        "complexity": complexity,
        "model": model,
        "effort": effort,
        "advisory_engine": advisory_engine,
        "chosen_via": chosen_via,
        "scores": scores,
        "excluded": excluded,
        "failover_reasons": failover_reasons,
        "applied_rules": applied_rules,
        "explanation": _explain(engine, role, complexity, model, chosen_via, excluded, failover_reasons),
    }


def _explain(engine, role, complexity, model, chosen_via, excluded, failover_reasons) -> str:
    parts = [
        "routed to {} ({} / {}) via {}".format(engine, role, model, chosen_via),
        "complexity {}".format(complexity),
    ]
    if excluded:
        parts.append("excluded: " + ", ".join(e["engine"] for e in excluded))
    if failover_reasons:
        parts.append("; ".join(failover_reasons))
    return ". ".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def cmd_route(args: argparse.Namespace) -> int:
    config = RouterConfig()
    if args.weekly_penalty is not None:
        config.weekly_usage_penalty = args.weekly_penalty
    if args.running_penalty is not None:
        config.running_now_penalty = args.running_penalty
    result = route(
        task_text=args.text,
        candidate_engines=(args.candidates.split(",") if args.candidates else None),
        override_engine=args.engine,
        role=args.role,
        config=config,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Semantic capability router with failover + load balancing")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("route", help="Route a task to the best available engine")
    p.add_argument("--text", required=True)
    p.add_argument("--engine", choices=VALID_ENGINES, help="Explicit override (wins)")
    p.add_argument("--role")
    p.add_argument("--candidates", help="Comma-separated subset of engines to consider")
    p.add_argument("--weekly-penalty", type=float, dest="weekly_penalty")
    p.add_argument("--running-penalty", type=float, dest="running_penalty")
    p.set_defaults(func=cmd_route)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
