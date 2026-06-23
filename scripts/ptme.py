#!/usr/bin/env python3
"""
ptme.py — real per-task model and effort decisions.

The capability table below is the single source of truth for model choice.
Complexity ladders point into that table; callers should not hand-maintain
separate model maps elsewhere.
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

CAPABILITY_TABLE = {
    "gemini-3.5-flash": {
        "family": "agy",
        "strengths": ["cheap drafting", "fast research bursts", "broad parallel work"],
        "cost_tier": "low",
    },
    "gemini-3.1-pro": {
        "family": "agy",
        "strengths": ["deep research", "long context synthesis", "parallel planning"],
        "cost_tier": "medium",
    },
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
}

RECOMMENDATION_LADDERS = {
    "default": {
        "S": ("gemini-3.5-flash", "low"),
        "M": ("gpt-5.3-codex", "medium"),
        "L": ("claude-sonnet-4.6", "high"),
        "XL": ("claude-opus-4.8", "high"),
    },
    "agy": {
        "S": ("gemini-3.5-flash", "low"),
        "M": ("gemini-3.1-pro", "medium"),
        "L": ("gemini-3.1-pro", "high"),
        "XL": ("gemini-3.1-pro", "high"),
    },
    "codex": {
        "S": ("gpt-5.3-codex", "low"),
        "M": ("gpt-5.3-codex", "medium"),
        "L": ("gpt-5.5", "high"),
        "XL": ("gpt-5.5", "high"),
    },
    "claude": {
        "S": ("claude-sonnet-4.6", "low"),
        "M": ("claude-sonnet-4.6", "medium"),
        "L": ("claude-sonnet-4.6", "high"),
        "XL": ("claude-opus-4.8", "high"),
    },
}

SIMPLE_SIGNALS = {
    "typo": -3,
    "rename": -2,
    "copy": -2,
    "label": -1,
    "heading": -1,
    "readme": -1,
    "docs": -1,
    "format": -1,
    "small": -1,
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
}

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
    hits = []
    for signal in signal_weights:
        if signal in text:
            hits.append(signal)
    return hits


def _complexity_score(task_text: str) -> tuple[int, list[str]]:
    normalized = " ".join((task_text or "").lower().split())
    tokens = TOKEN_RE.findall(normalized)
    score = 0
    reasons = []

    token_count = len(tokens)
    if token_count <= 8:
        score -= 2
        reasons.append("very short task text")
    elif token_count <= 20:
        reasons.append("short task text")
    elif token_count <= 45:
        score += 1
        reasons.append("medium task text")
    else:
        score += 2
        reasons.append("long task text")

    simple_hits = _signal_hits(normalized, SIMPLE_SIGNALS)
    for hit in simple_hits:
        score += SIMPLE_SIGNALS[hit]
    if simple_hits:
        reasons.append("simple signals: {}".format(", ".join(sorted(simple_hits))))

    complex_hits = _signal_hits(normalized, COMPLEX_SIGNALS)
    for hit in complex_hits:
        score += COMPLEX_SIGNALS[hit]
    if complex_hits:
        reasons.append("complex signals: {}".format(", ".join(sorted(complex_hits))))

    if normalized.count(" and ") >= 2 or normalized.count(";") >= 1:
        score += 1
        reasons.append("multi-part scope")

    return score, reasons


def classify_complexity(task_text: str) -> str:
    score, _reasons = _complexity_score(task_text)
    if score <= -1:
        return "S"
    if score <= 2:
        return "M"
    if score <= 5:
        return "L"
    return "XL"


def describe_complexity(task_text: str) -> str:
    score, reasons = _complexity_score(task_text)
    complexity = classify_complexity(task_text)
    detail = "; ".join(reasons) if reasons else "no strong signals"
    return "complexity {} (score {}: {})".format(complexity, score, detail)


def recommend_for_complexity(complexity: str, family: str | None = None) -> tuple[str, str]:
    ladder_key = family or "default"
    if ladder_key not in RECOMMENDATION_LADDERS:
        raise ValueError("Unknown recommendation family: {}".format(family))
    return RECOMMENDATION_LADDERS[ladder_key][complexity]


def _has_prior_failed_run(task_id: str, path: Path | None = None) -> bool:
    path = path or LOG_FILE
    for record in _load_records(path):
        if record.get("task_id") != task_id:
            continue
        exit_code = record.get("run_exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return True
        status = str(record.get("run_status", "")).lower()
        if status == "failed":
            return True
    return False


def decide(
    task_id,
    task_text,
    recommended_model=None,
    recommended_effort=None,
    override_model=None,
    override_effort=None,
    decided_by="local_orchestrator",
) -> dict:
    complexity = classify_complexity(task_text)
    default_model, default_effort = recommend_for_complexity(complexity)

    recommended_model = recommended_model or default_model
    recommended_effort = recommended_effort or default_effort
    decided_model = override_model or recommended_model
    decided_effort = override_effort or recommended_effort

    reason_parts = [
        describe_complexity(task_text),
    ]

    if recommended_model == default_model and recommended_effort == default_effort:
        reason_parts.append(
            "capability-table recommendation {} / {}".format(
                recommended_model, recommended_effort
            )
        )
    else:
        reason_parts.append(
            "caller recommendation {} / {} replaced default {} / {}".format(
                recommended_model,
                recommended_effort,
                default_model,
                default_effort,
            )
        )

    if override_model or override_effort:
        reason_parts.append(
            "override applied to {} / {}".format(decided_model, decided_effort)
        )

    if _has_prior_failed_run(task_id):
        reason_parts.append("prior failed run detected in ptme log")

    record = {
        "task_id": task_id,
        "complexity": complexity,
        "recommended_model": recommended_model,
        "recommended_effort": recommended_effort,
        "decided_model": decided_model,
        "decided_effort": decided_effort,
        "decided_by": decided_by,
        "reason": ". ".join(reason_parts),
        "ts": now_iso(),
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
    )
    print(json.dumps(record, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Real PTME model/effort decisions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_decide = subparsers.add_parser("decide", help="Compute and log a PTME decision")
    p_decide.add_argument("--task-id", required=True)
    p_decide.add_argument("--text", required=True)
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
