#!/usr/bin/env python3
"""
ptme.py — real per-task model and effort decisions, ENGINE-SCOPED.

CORE RULE (the bug this rebuild fixes): model selection is scoped to the engine.
A task dispatched to engine X is NEVER recommended a model from engine Y. There
is no cross-engine "default" ladder anymore — every recommendation goes through
the engine's own ladder, which only points at that engine's model family.

The capability table below is the single source of truth for model choice and
which family each model belongs to. Recommendation ladders point into it; the
module asserts at import time that each engine ladder only yields models whose
family matches that engine.

Everything is stdlib-only. No secrets, no subprocess, no network.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "logs" / "ptme_decisions.jsonl"

# ---------------------------------------------------------------------------
# Engine / model capability table (TUNABLE — edit model names here only).
# Each model declares the engine family it belongs to. The ladders below must
# only reference models whose family equals the ladder's engine; this is
# enforced by _assert_ladders_engine_scoped() at import time.
# ---------------------------------------------------------------------------
CAPABILITY_TABLE = {
    # --- Claude family ---
    "claude-haiku-4.5": {
        "family": "claude",
        "strengths": ["cheap fast edits", "small docs", "label/copy fixes"],
        "cost_tier": "low",
    },
    "claude-sonnet-4.6": {
        "family": "claude",
        "strengths": ["balanced reasoning", "coordination", "documentation"],
        "cost_tier": "medium",
    },
    "claude-opus-4.8": {
        "family": "claude",
        "strengths": ["architecture", "security review", "hard design judgment"],
        "cost_tier": "high",
    },
    # --- Codex / GPT family ---
    "gpt-5.3-codex": {
        "family": "codex",
        "strengths": ["single-file coding", "fast terminal work", "contained refactors"],
        "cost_tier": "medium",
    },
    "gpt-5.5": {
        "family": "codex",
        "strengths": ["heavier repo surgery", "multi-file coding", "complex bugfixes"],
        "cost_tier": "high",
    },
    # --- agy / Gemini family ---
    "gemini-3.5-flash": {
        "family": "agy",
        "strengths": ["cheap drafting", "fast research bursts", "broad parallel work"],
        "cost_tier": "low",
    },
    "gemini-3.1-pro": {
        "family": "agy",
        "strengths": ["deep research", "long context synthesis", "parallel planning"],
        "cost_tier": "high",
    },
}

# Per-engine recommendation ladders. NOTE: there is intentionally no longer a
# cross-engine "default" ladder — every engine maps complexity to ITS OWN
# family only. Effort is the second tuple element.
ENGINE_LADDERS = {
    "claude": {
        "S": ("claude-haiku-4.5", "low"),
        "M": ("claude-sonnet-4.6", "medium"),
        "L": ("claude-sonnet-4.6", "high"),
        "XL": ("claude-opus-4.8", "high"),
    },
    "codex": {
        "S": ("gpt-5.3-codex", "low"),
        "M": ("gpt-5.3-codex", "medium"),
        "L": ("gpt-5.5", "high"),
        "XL": ("gpt-5.5", "high"),
    },
    "agy": {
        "S": ("gemini-3.5-flash", "low"),
        "M": ("gemini-3.5-flash", "medium"),
        "L": ("gemini-3.1-pro", "high"),
        "XL": ("gemini-3.1-pro", "high"),
    },
}

# Backward-compatibility alias: older code imported RECOMMENDATION_LADDERS and
# expected a "default" key. We keep the name but the "default" entry is now an
# explicit, documented fallback that uses the Claude ladder ONLY when no engine
# is supplied. recommend_for_complexity() requires an engine in normal use; the
# fallback exists purely so legacy callers that pass family=None do not crash —
# and it still returns a single-family (claude) result, never a mixed one.
RECOMMENDATION_LADDERS = dict(ENGINE_LADDERS)
RECOMMENDATION_LADDERS["default"] = dict(ENGINE_LADDERS["claude"])

VALID_ENGINES = ("claude", "codex", "agy")


def _assert_ladders_engine_scoped() -> None:
    """Fail fast if any engine ladder points at a foreign-family model."""
    for engine, ladder in ENGINE_LADDERS.items():
        for complexity, (model, _effort) in ladder.items():
            info = CAPABILITY_TABLE.get(model)
            if info is None:
                raise AssertionError(
                    "ptme: ladder {}/{} references unknown model {!r}".format(
                        engine, complexity, model
                    )
                )
            if info["family"] != engine:
                raise AssertionError(
                    "ptme: ENGINE LEAK — engine {!r} ladder {} maps to {!r} "
                    "(family {!r})".format(engine, complexity, model, info["family"])
                )


_assert_ladders_engine_scoped()

# ---------------------------------------------------------------------------
# Role -> named specialist mapping (grounded in agents/roster.md). Per-engine
# teams are clones of this roster, so the same name maps across engines; the
# engine is carried separately on each record.
# ---------------------------------------------------------------------------
ROLE_SPECIALISTS = {
    "researcher": ("Researcher", "Information gathering, API/tech-stack analysis"),
    "coder": ("Coder", "Implementation, unit testing, modular architecture"),
    "qa": ("QA", "QA, responsive/accessibility verification, sign-off"),
    "security": ("Security", "Security & logic audit, deployment gating"),
    "designer": ("Designer", "UI/UX design systems, visual redesign, WCAG"),
    "content": ("Content", "Copywriting: CVs, docs, posts, proposals"),
    "data": ("Data", "SQLite/Supabase schemas, RLS, migrations"),
    "web": ("Web", "React/TS frontend, SEO, accessibility, performance"),
    "orchestrator": ("Root", "Task decomposition, delegation, pipeline mgmt"),
}


def specialist_for_role(role: str | None) -> tuple[str | None, str | None]:
    """Return (name, specialization) for a role, or (None, None) if unknown."""
    if not role:
        return None, None
    name, spec = ROLE_SPECIALISTS.get(str(role).strip().lower(), (None, None))
    return name, spec


# ---------------------------------------------------------------------------
# Complexity scoring (deterministic + explainable).
# ---------------------------------------------------------------------------
SIMPLE_SIGNALS = {
    "typo": -3,
    "rename": -2,
    "wire": -1,        # "wire N images/buttons" = mechanical plumbing
    "copy": -2,
    "label": -1,
    "heading": -1,
    "readme": -1,
    "docs": -1,
    "format": -1,
    "small": -1,
    "tweak": -1,
    "bump": -1,
    "swap": -1,
}

COMPLEX_SIGNALS = {
    "design": 2,
    "architecture": 3,
    "security": 3,
    "refactor": 2,
    "migration": 2,
    "parallel": 2,
    "multi-flight": 2,
    "orchestr": 2,
    "audit": 2,
    "rollout": 1,
    "infra": 1,
    "infrastructure": 1,
    "coordinator": 1,
    "concurrency": 2,
    "cross-file": 2,
    "shared": 1,
    "worker roster": 1,
    "end-to-end": 2,
    "schema": 1,
    "pipeline": 1,
}

# Risk words bump scope regardless of length.
RISK_SIGNALS = {
    "production": 2,
    "auth": 2,
    "secret": 2,
    "credential": 2,
    "delete": 1,
    "irreversible": 2,
    "rate limit": 1,
    "race condition": 2,
}

# Ambiguity words signal scope uncertainty -> nudge up.
AMBIGUITY_SIGNALS = ("etc", "and more", "as needed", "figure out", "somehow", "tbd", "various")

# Deliverable connectors: count distinct asks ("and", ";", "then", commas in lists).
DELIVERABLE_SPLIT_RE = re.compile(r"\b(?:and|then|also|plus)\b|[;]")

# File / path breadth: count file-ish tokens and directory hints.
FILE_TOKEN_RE = re.compile(r"[\w./-]+\.(?:py|js|ts|tsx|jsx|md|json|html|css|sql|yml|yaml|sh|ps1)\b", re.IGNORECASE)
PATH_HINT_RE = re.compile(r"\b\w+/\w+", re.IGNORECASE)

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*", re.IGNORECASE)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_record(record: dict, path: Path | None = None) -> None:
    path = path or LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _load_records(path: Path | None = None) -> list[dict]:
    path = path or LOG_FILE
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _signal_hits(text: str, signal_weights: dict[str, int]) -> list[str]:
    return [signal for signal in signal_weights if signal in text]


def _count_deliverables(normalized: str) -> int:
    """Estimate distinct deliverables from connector words/punctuation."""
    parts = DELIVERABLE_SPLIT_RE.split(normalized)
    meaningful = [p for p in parts if len(p.strip().split()) >= 2]
    return max(1, len(meaningful))


def semantic_complexity(task_text: str) -> str | None:
    """Phase-4 hook for real semantic routing.

    Returns a complexity bucket ("S"/"M"/"L"/"XL") when a semantic router is
    wired in, otherwise None. Intentionally a no-op stub now so the calling
    structure is ready: classify_complexity() consults this first and falls
    back to the deterministic scorer when it returns None.
    """
    return None


def _complexity_score(task_text: str) -> tuple[int, list[str]]:
    normalized = " ".join((task_text or "").lower().split())
    tokens = TOKEN_RE.findall(normalized)
    score = 0
    reasons: list[str] = []

    # 1) Length signal.
    token_count = len(tokens)
    if token_count <= 8:
        score -= 2
        reasons.append("very short text ({} words)".format(token_count))
    elif token_count <= 20:
        reasons.append("short text ({} words)".format(token_count))
    elif token_count <= 45:
        score += 1
        reasons.append("medium-length text ({} words)".format(token_count))
    else:
        score += 2
        reasons.append("long text ({} words)".format(token_count))

    # 2) Distinct deliverables.
    deliverables = _count_deliverables(normalized)
    if deliverables >= 4:
        score += 2
        reasons.append("{} distinct deliverables".format(deliverables))
    elif deliverables >= 2:
        score += 1
        reasons.append("{} distinct deliverables".format(deliverables))
    else:
        reasons.append("single deliverable")

    # 3) File / scope breadth.
    file_hits = set(FILE_TOKEN_RE.findall(normalized))
    path_hits = set(PATH_HINT_RE.findall(normalized))
    breadth = len(file_hits) + len(path_hits)
    if breadth >= 3:
        score += 2
        reasons.append("broad file scope ({} file/path refs)".format(breadth))
    elif breadth >= 1:
        # A single named file is usually a CONTAINED change — don't inflate it.
        reasons.append("{} file/path ref(s) (contained)".format(breadth))

    # 4) Simple signals (negative).
    simple_hits = _signal_hits(normalized, SIMPLE_SIGNALS)
    for hit in simple_hits:
        score += SIMPLE_SIGNALS[hit]
    if simple_hits:
        reasons.append("simple signals: {}".format(", ".join(sorted(simple_hits))))

    # 5) Complex signals (positive).
    complex_hits = _signal_hits(normalized, COMPLEX_SIGNALS)
    for hit in complex_hits:
        score += COMPLEX_SIGNALS[hit]
    if complex_hits:
        reasons.append("complex signals: {}".format(", ".join(sorted(complex_hits))))

    # 6) Risk words.
    risk_hits = _signal_hits(normalized, RISK_SIGNALS)
    for hit in risk_hits:
        score += RISK_SIGNALS[hit]
    if risk_hits:
        reasons.append("risk signals: {}".format(", ".join(sorted(risk_hits))))

    # 7) Ambiguity.
    ambiguity_hits = [w for w in AMBIGUITY_SIGNALS if w in normalized]
    if ambiguity_hits:
        score += 1
        reasons.append("ambiguity: {}".format(", ".join(ambiguity_hits)))

    return score, reasons


def _consult_promoted_rules(task_text: str, complexity: str) -> str:
    """Guarded Phase-3 consultation: a promoted 'keep_complexity' rule whose
    condition (complexity + signal) matches confirms the classification.

    An empty or missing promoted_rules.json is a strict no-op (returns the input
    complexity unchanged). Any import/IO error is swallowed so ptme never breaks.
    """
    try:
        import learning_loop
    except Exception:
        return complexity
    try:
        normalized = " ".join((task_text or "").lower().split())
        signals = [s for s in SIMPLE_SIGNALS if s in normalized]
        ctx = {"complexity": complexity, "signals": signals}
        for rule in learning_loop.consult(ctx):
            keep = rule.get("adjustment", {}).get("keep_complexity")
            if keep in ("S", "M", "L", "XL"):
                return keep
    except Exception:
        return complexity
    return complexity


def classify_complexity(task_text: str) -> str:
    semantic = semantic_complexity(task_text)
    if semantic in ("S", "M", "L", "XL"):
        return semantic
    score, _reasons = _complexity_score(task_text)
    if score <= -1:
        base = "S"
    elif score <= 2:
        base = "M"
    elif score <= 5:
        base = "L"
    else:
        base = "XL"
    return _consult_promoted_rules(task_text, base)


def describe_complexity(task_text: str) -> str:
    score, reasons = _complexity_score(task_text)
    complexity = classify_complexity(task_text)
    detail = "; ".join(reasons) if reasons else "no strong signals"
    thresholds = "S<=-1 / M<=2 / L<=5 / XL>5"
    return "complexity {} (score {} [{}]): {}".format(complexity, score, thresholds, detail)


def recommend_for_complexity(complexity: str, family: str | None = None) -> tuple[str, str]:
    """Engine-scoped recommendation. `family` is the engine id.

    Passing a known engine ("claude"/"codex"/"agy") returns a model from THAT
    engine only. family=None falls back to RECOMMENDATION_LADDERS["default"]
    (the Claude ladder) for legacy callers, but emits a single-family result.
    """
    ladder_key = family or "default"
    if ladder_key not in RECOMMENDATION_LADDERS:
        raise ValueError("Unknown recommendation family/engine: {}".format(family))
    return RECOMMENDATION_LADDERS[ladder_key][complexity]


def _has_prior_failed_run(task_id: str, path: Path | None = None) -> bool:
    path = path or LOG_FILE
    for record in _load_records(path):
        if record.get("task_id") != task_id:
            continue
        exit_code = record.get("run_exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return True
        if str(record.get("run_status", "")).lower() == "failed":
            return True
    return False


def estimate_planned_tokens(complexity: str, engine: str | None = None) -> int:
    """Rough, labelled planned-token ESTIMATE per task (not a measurement).

    Order-of-magnitude only; the dashboard labels it 'planned'. Codex sessions
    accumulate more per turn, so the codex estimate is higher.
    """
    base = {"S": 8000, "M": 25000, "L": 80000, "XL": 160000}.get(complexity, 25000)
    if engine == "codex":
        base = int(base * 1.5)
    return base


def decide(
    task_id,
    task_text,
    recommended_model=None,
    recommended_effort=None,
    override_model=None,
    override_effort=None,
    decided_by="local_orchestrator",
    engine: str | None = None,
    role: str | None = None,
    tester_role: str | None = None,
    received_at: str | None = None,
) -> dict:
    """Compute and persist an engine-scoped PTME decision record.

    `engine` scopes model selection. When omitted (legacy callers), the engine
    is inferred from a supplied recommended_model's family, else falls back to
    the documented default ladder. The recorded record carries the full schema:
    engine, role, assigned_name, complexity, score, score_reasons,
    recommended_model/effort, override_model/effort, decided_model/effort,
    tester_role/tester_name, planned_tokens, timestamps, and labelled token
    scale fields.
    """
    complexity = classify_complexity(task_text)
    score, score_reasons = _complexity_score(task_text)

    # Resolve engine: explicit arg wins; else infer from a recommended model's
    # family; else None -> default ladder (claude family).
    resolved_engine = engine
    if resolved_engine is None and recommended_model:
        info = CAPABILITY_TABLE.get(recommended_model)
        if info:
            resolved_engine = info["family"]

    ladder_engine = resolved_engine if resolved_engine in VALID_ENGINES else None
    default_model, default_effort = recommend_for_complexity(complexity, family=ladder_engine)

    recommended_model = recommended_model or default_model
    recommended_effort = recommended_effort or default_effort

    # Guard: a caller-supplied recommendation from a FOREIGN family is rejected
    # back to the engine-scoped default, with the leak noted in the reason.
    engine_leak = None
    if ladder_engine is not None:
        rec_info = CAPABILITY_TABLE.get(recommended_model)
        if rec_info and rec_info["family"] != ladder_engine:
            engine_leak = (recommended_model, rec_info["family"])
            recommended_model, recommended_effort = default_model, default_effort

    decided_model = override_model or recommended_model
    decided_effort = override_effort or recommended_effort

    # Override too must respect engine scope: a foreign-family override is
    # rejected (it would mean dispatching engine X to engine Y's model).
    override_leak = None
    if override_model and ladder_engine is not None:
        ov_info = CAPABILITY_TABLE.get(override_model)
        if ov_info and ov_info["family"] != ladder_engine:
            override_leak = (override_model, ov_info["family"])
            decided_model = recommended_model  # ignore the foreign override

    name, specialization = specialist_for_role(role)
    tester_name, _tester_spec = specialist_for_role(tester_role)

    # --- Orchestrator judgment (FIX 3): a real ACCEPT or OVERRIDE decision ---
    # The local/per-engine orchestrator either accepts the engine-scoped PTME
    # recommendation or overrides it with a logged rationale. This is rule-based
    # but genuine: a foreign-family leak, an explicit override, or a prior failed
    # run all constitute real reasons to override. When the orchestrator is
    # recommending to ITSELF (the top orchestrator 'root', or no distinct caller
    # recommendation), we record a single clean rationale rather than the
    # redundant 'caller recommendation X replaced default Y' wording.
    judged_by = decided_by or "local_orchestrator"
    is_self_orchestrator = str(judged_by).strip().lower() in ("root", "local_orchestrator")
    caller_recommended = bool(override_model or override_effort or engine_leak or override_leak)
    judgment = "overridden" if (override_model or override_effort) and not override_leak else "accepted"

    rationale_parts = [describe_complexity(task_text)]
    if resolved_engine:
        rationale_parts.append("engine {}".format(resolved_engine))
    if engine_leak:
        rationale_parts.append(
            "rejected foreign recommendation {} (family {}) — using engine "
            "default {}".format(engine_leak[0], engine_leak[1], default_model)
        )
        judgment = "overridden"
    # Clean self-recommendation wording: only narrate a caller-vs-default swap
    # when there genuinely was a distinct external recommendation. Recommending
    # to itself records a single clean decision rationale.
    if recommended_model == default_model and recommended_effort == default_effort:
        rationale_parts.append(
            "engine-scoped decision {} / {}".format(decided_model, decided_effort)
        )
    elif is_self_orchestrator and not caller_recommended:
        rationale_parts.append(
            "decision {} / {}".format(decided_model, decided_effort)
        )
    else:
        rationale_parts.append(
            "caller recommendation {} / {} replaced engine default {} / {}".format(
                recommended_model, recommended_effort, default_model, default_effort
            )
        )
    if override_leak:
        rationale_parts.append(
            "rejected foreign override {} (family {}) — kept {}".format(
                override_leak[0], override_leak[1], decided_model
            )
        )
    elif override_model or override_effort:
        rationale_parts.append("override applied to {} / {}".format(decided_model, decided_effort))
        judgment = "overridden"
    if _has_prior_failed_run(task_id):
        rationale_parts.append("prior failed run detected in ptme log")
        judgment = "overridden"

    rationale = ". ".join(rationale_parts)
    reason_parts = rationale_parts

    ts = now_iso()
    planned_tokens = estimate_planned_tokens(complexity, resolved_engine)

    record = {
        "task_id": task_id,
        "engine": resolved_engine,
        "role": role,
        "assigned_name": name,
        "specialization": specialization,
        "complexity": complexity,
        "score": score,
        "score_reasons": score_reasons,
        "recommended_model": recommended_model,
        "recommended_effort": recommended_effort,
        "override_model": override_model,
        "override_effort": override_effort,
        "decided_model": decided_model,
        "decided_effort": decided_effort,
        "decided_by": decided_by,
        # Orchestrator judgment (FIX 3): a real accept/override decision + why.
        "judgment": judgment,
        "rationale": rationale,
        "tester_role": tester_role,
        "tester_name": tester_name,
        # planned vs actual: planned is an ESTIMATE (labelled); actual filled on complete.
        "planned_tokens": planned_tokens,
        "actual_tokens": None,
        # token scale labels (E): per-task vs whole-session cumulative.
        "tokens_task": None,
        "tokens_session_cumulative": None,
        "duration_ms": None,
        "usage_window": None,
        # timestamps (D): received_at = dispatch; finished_at set on complete.
        "received_at": received_at or ts,
        "finished_at": None,
        "reason": ". ".join(reason_parts),
        "ts": ts,
    }
    append_record(record, LOG_FILE)
    return record


def cmd_decide(args: argparse.Namespace) -> int:
    record = decide(
        task_id=args.task_id,
        task_text=args.text,
        recommended_model=args.recommend_model,
        recommended_effort=args.recommend_effort,
        override_model=args.override_model,
        override_effort=args.override_effort,
        decided_by=args.by,
        engine=args.engine,
        role=args.role,
        tester_role=args.tester_role,
    )
    print(json.dumps(record, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Real PTME model/effort decisions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_decide = subparsers.add_parser("decide", help="Compute and log a PTME decision")
    p_decide.add_argument("--task-id", required=True)
    p_decide.add_argument("--text", required=True)
    p_decide.add_argument("--engine", choices=VALID_ENGINES)
    p_decide.add_argument("--role")
    p_decide.add_argument("--tester-role")
    p_decide.add_argument("--recommend-model")
    p_decide.add_argument("--recommend-effort")
    p_decide.add_argument("--override-model")
    p_decide.add_argument("--override-effort")
    p_decide.add_argument("--by", default="local_orchestrator")
    p_decide.set_defaults(func=cmd_decide)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
