#!/usr/bin/env python3
"""
agent_config.py — Load and display per-agent TOML config with project-level overrides.

Loads _defaults.toml first, then deep-merges the agent-specific TOML on top.

Usage:
    python scripts/agent_config.py get  --agent root  --key agent.max_task_size
    python scripts/agent_config.py show --agent codex
    python scripts/agent_config.py list-agents
"""

# BUG-3: Python 3.8 compatibility — enables 'list[str]' return annotations
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# QA-1: Import shared utilities from config_loader instead of duplicating them
from config_loader import (
    ConfigLoadError,
    deep_merge,
    get_nested as _cl_get_nested,
    list_agent_names as _cl_list_agent_names,
    load_toml as _cl_load_toml,
    safe_agent_path as _cl_safe_agent_path,
)

ROOT       = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config" / "agents"
DEFAULTS   = CONFIG_DIR / "_defaults.toml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_toml(path: Path) -> dict:
    """Load a TOML file. Returns {} if the file does not exist.
    Calls sys.exit(1) on parse error (agent_config commands are CLI-facing).
    """
    try:
        return _cl_load_toml(path)
    except ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


def _safe_agent_path(agent_name: str) -> Path:
    """Resolve the TOML path for *agent_name*, rejecting path-traversal attempts."""
    try:
        return _cl_safe_agent_path(CONFIG_DIR, agent_name)
    except ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


def load_agent_config(agent_name: str) -> dict:
    """Return the fully-merged config for *agent_name* (defaults + overrides)."""
    defaults   = load_toml(DEFAULTS)
    agent_file = _safe_agent_path(agent_name)
    overrides  = load_toml(agent_file)
    return deep_merge(defaults, overrides)


def list_agent_names() -> list[str]:
    """Return sorted list of agent names (TOML stem, excluding _defaults)."""
    return _cl_list_agent_names(CONFIG_DIR)


def get_nested(config: dict, dotkey: str):
    """Resolve a dot-notation key like 'agent.max_task_size' from a nested dict."""
    return _cl_get_nested(config, dotkey)


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def _fmt_value(val) -> str:
    if isinstance(val, list):
        if not val:
            return "[]"
        items = ", ".join('"{}"'.format(v) if isinstance(v, str) else str(v) for v in val)
        return "[{}]".format(items)
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, str):
        return '"{}"'.format(val)
    return str(val)


def _print_config_table(config: dict, agent_name: str):
    """Print config as a human-readable table."""
    print("Agent config: {}\n".format(agent_name))
    print("  {:<22}  {:<20}  {}".format("SECTION", "KEY", "VALUE"))
    print("  " + "-" * 70)
    for section, vals in config.items():
        if isinstance(vals, dict):
            for key, val in vals.items():
                print("  {:<22}  {:<20}  {}".format(
                    "[{}]".format(section), key, _fmt_value(val)
                ))
        else:
            print("  {:<22}  {:<20}  {}".format("(root)", section, _fmt_value(vals)))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_get(args):
    agent_file = _safe_agent_path(args.agent)
    if not agent_file.exists():
        print("[ERROR] No config file for agent '{}' ({}).".format(args.agent, agent_file),
              file=sys.stderr)
        sys.exit(1)

    config = load_agent_config(args.agent)
    val = get_nested(config, args.key)
    if val is None:
        print("[NOT FOUND] Key '{}' not found in config for agent '{}'.".format(
            args.key, args.agent))
        sys.exit(2)
    print(_fmt_value(val))


def cmd_show(args):
    agent_file = _safe_agent_path(args.agent)
    if not agent_file.exists():
        print("[ERROR] No config file for agent '{}' ({}).".format(args.agent, agent_file),
              file=sys.stderr)
        sys.exit(1)

    config = load_agent_config(args.agent)
    _print_config_table(config, args.agent)


def cmd_list_agents(args):
    agents = list_agent_names()
    if not agents:
        print("No agent config files found in {}.".format(CONFIG_DIR))
        return
    print("Configured agents ({}):\n".format(len(agents)))
    for name in agents:
        print("  {}".format(name))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="agent_config.py",
        description="Load and display per-agent config (TOML with project-level overrides).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # get
    p_get = sub.add_parser("get", help="Return a single config value for an agent.")
    p_get.add_argument("--agent", required=True, help="Agent name (e.g. root, codex)")
    p_get.add_argument("--key",   required=True,
                       help="Dot-notation key, e.g. agent.max_task_size")

    # show
    p_show = sub.add_parser("show", help="Print all merged config for an agent.")
    p_show.add_argument("--agent", required=True, help="Agent name")

    # list-agents
    sub.add_parser("list-agents", help="List all configured agents.")

    args = parser.parse_args()

    dispatch = {
        "get":         cmd_get,
        "show":        cmd_show,
        "list-agents": cmd_list_agents,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
