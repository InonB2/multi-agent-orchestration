#!/usr/bin/env python3
"""
routing_table.py — small role -> engine advisory table for capability routing.

Advisory only: callers may use this to bias tie-breaks or explain why a role
leans toward a given engine. It never hard-routes a task by itself.
"""

from __future__ import annotations

VALID_ENGINES = ("claude", "codex", "agy")
DEFAULT_ENGINE_ORDER = VALID_ENGINES

ROLE_ENGINE_ADVISORY = {
    "researcher": ("agy", "claude", "codex"),
    "coder": ("codex", "claude", "agy"),
    "qa": ("codex", "claude", "agy"),
    "security": ("claude", "codex", "agy"),
    "designer": ("claude", "agy", "codex"),
    "content": ("agy", "claude", "codex"),
    "data": ("codex", "agy", "claude"),
    "web": ("codex", "claude", "agy"),
    "orchestrator": ("claude", "agy", "codex"),
}


def advisory_engines_for_role(role: str | None) -> tuple[str, ...]:
    normalized = str(role or "").strip().lower()
    return ROLE_ENGINE_ADVISORY.get(normalized, DEFAULT_ENGINE_ORDER)


def advisory_engine_for_role(role: str | None) -> str:
    return advisory_engines_for_role(role)[0]


def advisory_rank(role: str | None, engine: str) -> int:
    engines = advisory_engines_for_role(role)
    try:
        return engines.index(str(engine or "").strip().lower())
    except ValueError:
        return len(engines)
