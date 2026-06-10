#!/usr/bin/env python3
"""
config_loader.py — Shared configuration utilities for multi-agent-orchestration.

Provides:
  ConfigLoadError    — raised on TOML parse or path errors (never calls sys.exit)
  load_toml()        — load a TOML file, raise ConfigLoadError on failure
  deep_merge()       — recursively merge two config dicts
  get_nested()       — resolve dot-notation keys from a nested dict
  list_agent_names() — enumerate TOML-based agent names in a directory
  safe_agent_path()  — path-traversal-safe resolution of an agent's TOML file

Callers are responsible for catching ConfigLoadError and deciding whether to
print an error and sys.exit(1) (commands) or print an ERROR row (listing loops).
"""

from __future__ import annotations

import re
import sys
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
        print(
            "[ERROR] TOML library not available.\n"
            "  Python 3.11+ ships 'tomllib' in the stdlib.\n"
            "  For older Python: pip install tomli",
            file=sys.stderr,
        )
        sys.exit(1)

# Agent name allowlist: alphanumeric, hyphens, underscores only
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class ConfigLoadError(Exception):
    """Raised when a configuration file cannot be loaded or parsed."""


def load_toml(path: Path) -> dict:
    """Load a TOML file and return the parsed dict.

    Returns {} if the file does not exist.
    Raises ConfigLoadError if the file exists but cannot be parsed.
    Never calls sys.exit — the caller decides how to handle the error.
    """
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigLoadError("[ERROR] Failed to parse {}: {}".format(path, exc)) from exc


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*. Returns a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def get_nested(d: dict, dotkey: str, default=None):
    """Resolve a dot-notation key like 'agent.max_task_size' from a nested dict."""
    cur = d
    for part in dotkey.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def list_agent_names(config_dir: Path) -> list:
    """Return sorted list of agent names (TOML stem, excluding _defaults)."""
    if not config_dir.exists():
        return []
    return sorted(p.stem for p in config_dir.glob("*.toml") if p.stem != "_defaults")


def safe_agent_path(config_dir: Path, agent_name: str) -> Path:
    """Resolve the TOML path for *agent_name*, rejecting path-traversal attempts.

    Validates agent_name against the allowlist regex, then checks that the resolved
    path remains inside config_dir (belt-and-suspenders against symlink attacks).

    Raises ConfigLoadError on invalid names or detected traversal.
    """
    if not _AGENT_NAME_RE.match(agent_name):
        raise ConfigLoadError(
            "[ERROR] Invalid agent name '{}'. "
            "Only alphanumeric characters, hyphens, and underscores are allowed.".format(
                agent_name
            )
        )
    candidate = (config_dir / "{}.toml".format(agent_name.lower())).resolve()
    try:
        candidate.relative_to(config_dir.resolve())
    except ValueError:
        raise ConfigLoadError(
            "[ERROR] Invalid agent name '{}' — path traversal detected.".format(agent_name)
        )
    return candidate
