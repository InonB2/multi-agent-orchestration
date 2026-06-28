#!/usr/bin/env python3
"""
role_scoring.py — shared, transparent role inference for the orchestration layer.

WHY THIS EXISTS
---------------
Both router.infer_role() and sub_orchestrator._route_role() used to walk a dict
of role -> keyword tuples FIRST-HIT-WINS with `coder` checked LAST. That made a
build/implement task get stolen by an incidental secondary mention:

    "refactor + add tests"                 -> qa        (WRONG, should be coder)
    "refactor the auth module + tests"     -> security  (WRONG, should be coder)

A primary build intent ("refactor", "implement", "add") must NOT lose to a
secondary clause that merely mentions "test"/"auth"/"secure"/"design".

THE FIX — transparent scored intent
-----------------------------------
Each role advertises two signal tiers:

  * PRIMARY signals — phrases/verbs that, when present, are a strong statement
    that THIS role is the point of the task ("qa the", "security audit",
    "research", "design a", and for `coder` the build/implement verbs).
  * SECONDARY signals — weaker keywords that often appear as an incidental
    clause ("test", "auth", "secure", "ui", "schema", ...). They nudge but do
    not by themselves outrank a primary build verb.

score(role) = PRIMARY_WEIGHT * (#primary matches) + (#secondary matches)

The role with the max score wins; ties break by a deterministic ROLE_PRIORITY
order. `coder` is the safe default when nothing matches (most build work is
coding). The same function backs both call sites so they can never drift.

stdlib-only. No secrets, no subprocess, no network.
"""

from __future__ import annotations

# Weight of a primary (verb / leading-phrase) signal relative to a secondary
# keyword. 10 is comfortably larger than the number of secondary keywords any
# realistic task can match, so ONE clear primary intent always beats a pile of
# incidental secondary mentions, while a genuine primary phrase for another role
# (also worth PRIMARY_WEIGHT) can still win head-to-head.
PRIMARY_WEIGHT = 10

# Deterministic tiebreak when two roles score equal. Earlier = preferred. coder
# sits LAST so a real specialist primary intent wins a tie, but coder remains the
# global default (handled separately) when nothing matched at all.
ROLE_PRIORITY = (
    "security",
    "qa",
    "researcher",
    "designer",
    "data",
    "web",
    "content",
    "coder",
)

# Primary signals: a strong statement that the task's POINT is this role.
# Build/implement verbs make `coder` primary so incidental "test"/"auth"/"secure"
# clauses cannot steal an implementation task.
PRIMARY_SIGNALS: dict[str, tuple[str, ...]] = {
    "security": (
        "security audit", "security review", "security test", "pentest",
        "penetration test", "harden", "threat model", "attack surface",
        "vulnerability scan", "secrets audit", "audit the security",
    ),
    "qa": (
        "qa the", "qa of", "qa on", "test the", "verify the", "validate the",
        "sign off", "sign-off", "acceptance test", "regression test",
        "reproduce the", "quality assurance", "smoke test",
    ),
    "researcher": (
        "research", "investigate", "look into", "find out", "evaluate options",
        "compare the", "benchmark the", "survey the", "explore the",
    ),
    "designer": (
        "design a", "design the", "design an", "wireframe", "mockup",
        "ux for", "ui for", "lay out", "visual design", "redesign",
    ),
    "data": (
        "schema for", "migration for", "data model", "database schema",
        "design the schema", "migrate the", "etl", "build the dataset",
    ),
    "web": (
        "dashboard", "landing page", "web page", "frontend for",
        "render the page", "html page", "css for",
    ),
    "content": (
        "write the", "write a", "write up", "document the", "draft the",
        "draft a", "playbook", "readme", "blog post", "copywrite",
    ),
    "coder": (
        # Build / implement verbs — the PRIMARY intent for engineering work.
        "refactor", "implement", "build", "create", "add ", "write code",
        "code up", "fix ", "wire ", "patch", "develop", "rewrite", "scaffold",
        "integrate", "hook up",
    ),
}

# Secondary signals: weak keywords that often appear in an incidental clause.
# They contribute 1 point each — they refine ties and break true ambiguity, but
# never outweigh a single primary intent.
SECONDARY_SIGNALS: dict[str, tuple[str, ...]] = {
    "security": ("security", "secret", "auth", "vulnerab", "exploit", "secure"),
    "qa": ("qa", "test", "verify", "validate", "reproduce", "coverage"),
    "researcher": ("research", "investigate", "compare", "evaluate", "gather", "explore", "benchmark"),
    "designer": ("design", "ui", "ux", "layout", "wireframe", "mockup", "component"),
    "data": ("schema", "migration", "query", "database", "sql", "table", "dataset"),
    "web": ("dashboard", "frontend", "page", "html", "css", "render", "feed"),
    "content": ("document", "write", "doc", "playbook", "readme", "memo", "copy", "draft", "post"),
    "coder": ("implement", "build", "code", "script", "refactor", "fix", "wire", "module", "patch"),
}


def score_roles(task_text: str) -> dict[str, float]:
    """Return {role: score} for every role. Transparent and deterministic.

    score = PRIMARY_WEIGHT * (#distinct primary signals matched)
            +              (#distinct secondary signals matched)
    """
    text = " " + (task_text or "").lower() + " "
    scores: dict[str, float] = {}
    for role in ROLE_PRIORITY:
        primary = sum(1 for sig in PRIMARY_SIGNALS.get(role, ()) if sig in text)
        secondary = sum(1 for sig in SECONDARY_SIGNALS.get(role, ()) if sig in text)
        scores[role] = PRIMARY_WEIGHT * primary + secondary
    return scores


def infer_role(task_text: str, default: str = "coder") -> str:
    """Pick the single best role for a task.

    Max score wins; ROLE_PRIORITY breaks ties deterministically. If NOTHING
    matched (all zero) we fall back to `default` (coder) — most build work is
    coding.
    """
    scores = score_roles(task_text)
    best = max(scores.values())
    if best <= 0:
        return default
    # Deterministic: among max-scorers, take the one earliest in ROLE_PRIORITY.
    for role in ROLE_PRIORITY:
        if scores[role] == best:
            return role
    return default  # pragma: no cover - unreachable
