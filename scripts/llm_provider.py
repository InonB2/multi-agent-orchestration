#!/usr/bin/env python3
"""
llm_provider.py — LLM provider abstraction for the multi-agent orchestration framework.

Supports two provider types:
  cli  — delegates task execution to a local AI CLI tool (Claude Code, Codex CLI, etc.)
  api  — calls any OpenAI-compatible REST endpoint directly (OpenAI, Anthropic, local models)

Usage:
  # Get provider config for an agent
  python scripts/llm_provider.py info --agent codex

  # Execute a task prompt via the configured provider
  python scripts/llm_provider.py run --agent codex --prompt "Review this code..."

  # List all agents and their provider types
  python scripts/llm_provider.py list
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
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

ROOT       = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config" / "agents"
DEFAULTS   = CONFIG_DIR / "_defaults.toml"

# Agent name must be alphanumeric + hyphens + underscores only (path traversal guard)
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_agent_name(name: str) -> None:
    """Reject agent names that contain path-traversal or invalid characters."""
    if not _AGENT_NAME_RE.match(name):
        print(
            "[ERROR] Invalid agent name '{}'. "
            "Only alphanumeric characters, hyphens, and underscores are allowed.".format(name),
            file=sys.stderr,
        )
        sys.exit(1)


def _load_toml(path: Path) -> dict:
    """Load a TOML file. Returns {} if the file does not exist."""
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print("[ERROR] Failed to parse {}: {}".format(path, exc), file=sys.stderr)
        sys.exit(1)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*. Returns a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _agent_toml_path(agent_name: str) -> Path:
    """Resolve the TOML path for *agent_name*, rejecting path-traversal attempts."""
    _validate_agent_name(agent_name)
    candidate = (CONFIG_DIR / "{}.toml".format(agent_name.lower())).resolve()
    try:
        candidate.relative_to(CONFIG_DIR.resolve())
    except ValueError:
        print("[ERROR] Invalid agent name — path traversal detected.", file=sys.stderr)
        sys.exit(1)
    return candidate


def _load_agent_config(agent_name: str) -> dict:
    """Return the fully-merged config for *agent_name* (defaults + agent overrides)."""
    defaults   = _load_toml(DEFAULTS)
    agent_file = _agent_toml_path(agent_name)
    overrides  = _load_toml(agent_file)
    return _deep_merge(defaults, overrides)


def _get_nested(config: dict, dotkey: str):
    """Resolve a dot-notation key like 'provider.type' from a nested dict."""
    cur = config
    for part in dotkey.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _list_agent_names() -> list:
    """Return sorted list of agent names (TOML stem, excluding _defaults)."""
    if not CONFIG_DIR.exists():
        return []
    return sorted(p.stem for p in CONFIG_DIR.glob("*.toml") if p.stem != "_defaults")


def _is_anthropic(base_url: str) -> bool:
    """Return True when the base URL points to the Anthropic API."""
    return "api.anthropic.com" in base_url


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_info(args) -> None:
    """Print provider configuration for a single agent."""
    agent_name = args.agent
    _validate_agent_name(agent_name)

    agent_file = _agent_toml_path(agent_name)
    if not agent_file.exists():
        print(
            "[ERROR] No config file for agent '{}' ({}).".format(agent_name, agent_file),
            file=sys.stderr,
        )
        sys.exit(1)

    config   = _load_agent_config(agent_name)
    provider = config.get("provider", {})
    ptype    = provider.get("type", "cli")

    print("Agent:         {}".format(agent_name))
    print("Provider type: {}".format(ptype))

    if ptype == "cli":
        cli_cmd = config.get("agent", {}).get("preferred_model", agent_name)
        print("CLI tool:      {}".format(cli_cmd))

    elif ptype == "api":
        api_base_url = provider.get("api_base_url")
        model_id     = provider.get("model_id")
        key_env      = provider.get("api_key_env_var")

        if not api_base_url:
            print(
                "[ERROR] api_base_url is not configured for agent '{}'. "
                "Set it in the [provider] section of {}.toml.".format(agent_name, agent_name),
                file=sys.stderr,
            )
            sys.exit(1)

        print("API base URL:  {}".format(api_base_url))
        print("Model:         {}".format(model_id or "(not set)"))

        if key_env:
            key_status = "SET" if os.environ.get(key_env) else "NOT SET"
            print("API key env:   {} [{}]".format(key_env, key_status))
        else:
            print("API key env:   (not configured)")

        if _is_anthropic(api_base_url):
            print("Auth format:   Anthropic (x-api-key + anthropic-version headers)")
        else:
            print("Auth format:   OpenAI-compatible (Authorization: Bearer)")

    else:
        print("[WARNING] Unknown provider type: {}".format(ptype))


def cmd_run(args) -> None:
    """Execute a task prompt via the agent's configured provider."""
    agent_name = args.agent
    prompt     = args.prompt
    dry_run    = args.dry_run

    _validate_agent_name(agent_name)

    agent_file = _agent_toml_path(agent_name)
    if not agent_file.exists():
        print(
            "[ERROR] No config file for agent '{}' ({}).".format(agent_name, agent_file),
            file=sys.stderr,
        )
        sys.exit(1)

    config   = _load_agent_config(agent_name)
    provider = config.get("provider", {})
    ptype    = provider.get("type", "cli")

    # ---- CLI mode ----
    if ptype == "cli":
        cli_cmd = config.get("agent", {}).get("preferred_model", agent_name)
        print("CLI command: {} \"{}\"".format(cli_cmd, prompt))

        if not dry_run:
            try:
                result = subprocess.run(
                    [cli_cmd, prompt],
                    capture_output=True,
                    text=True,
                )
                if result.stdout:
                    print(result.stdout)
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
                sys.exit(result.returncode)
            except FileNotFoundError:
                print(
                    "[ERROR] CLI tool '{}' not found on PATH.".format(cli_cmd),
                    file=sys.stderr,
                )
                sys.exit(1)
        return

    # ---- API mode ----
    if ptype == "api":
        api_base_url = provider.get("api_base_url")
        model_id     = provider.get("model_id")
        key_env      = provider.get("api_key_env_var")

        if not api_base_url:
            print(
                "[ERROR] api_base_url is not configured for agent '{}'. "
                "Set it in the [provider] section of {}.toml.".format(agent_name, agent_name),
                file=sys.stderr,
            )
            sys.exit(1)

        if not key_env:
            print(
                "[ERROR] api_key_env_var is not configured for agent '{}'.".format(agent_name),
                file=sys.stderr,
            )
            sys.exit(1)

        is_anth = _is_anthropic(api_base_url)

        if is_anth:
            endpoint = "{}/messages".format(api_base_url.rstrip("/"))
            payload  = {
                "model":      model_id,
                "max_tokens": 1024,
                "messages":   [{"role": "user", "content": prompt}],
            }
        else:
            endpoint = "{}/chat/completions".format(api_base_url.rstrip("/"))
            payload  = {
                "model":    model_id,
                "messages": [{"role": "user", "content": prompt}],
            }

        # --dry-run: print request details without sending (no API key needed)
        if dry_run:
            print("API request (dry-run):")
            print("  Endpoint:    {}".format(endpoint))
            print("  Model:       {}".format(model_id))
            print("  Auth env:    {}".format(key_env))
            print("  Auth format: {}".format(
                "Anthropic (x-api-key)" if is_anth else "OpenAI-compatible (Bearer)"
            ))
            print("  Payload:")
            for line in json.dumps(payload, indent=4).splitlines():
                print("    {}".format(line))
            return

        # Live run — resolve and validate API key
        api_key = os.environ.get(key_env)
        if not api_key:
            print(
                "[ERROR] API key env var '{}' is not set. "
                "Export it before running in live mode.".format(key_env),
                file=sys.stderr,
            )
            sys.exit(1)

        if is_anth:
            headers = {
                "Content-Type":    "application/json",
                "x-api-key":       api_key,
                "anthropic-version": "2023-06-01",
            }
        else:
            headers = {
                "Content-Type":  "application/json",
                "Authorization": "Bearer {}".format(api_key),
            }

        body = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if is_anth:
                    # Anthropic Messages API: data["content"][0]["text"]
                    content = data.get("content", [])
                    if content and isinstance(content, list):
                        print(content[0].get("text", ""))
                    else:
                        print(json.dumps(data, indent=2))
                else:
                    # OpenAI Chat Completions: data["choices"][0]["message"]["content"]
                    choices = data.get("choices", [])
                    if choices:
                        print(choices[0].get("message", {}).get("content", ""))
                    else:
                        print(json.dumps(data, indent=2))
        except urllib.error.HTTPError as exc:
            body_err = exc.read().decode("utf-8", errors="replace")
            print(
                "[ERROR] HTTP {} from {}: {}".format(exc.code, endpoint, body_err),
                file=sys.stderr,
            )
            sys.exit(1)
        except urllib.error.URLError as exc:
            print(
                "[ERROR] Failed to connect to {}: {}".format(endpoint, exc.reason),
                file=sys.stderr,
            )
            sys.exit(1)
        return

    print(
        "[ERROR] Unknown provider type '{}' for agent '{}'.".format(ptype, agent_name),
        file=sys.stderr,
    )
    sys.exit(1)


def cmd_list(args) -> None:
    """Print all agents and their provider types in a table."""
    agents = _list_agent_names()
    if not agents:
        print("No agent config files found in {}.".format(CONFIG_DIR))
        return

    print("{:<25} {:<8} {}".format("AGENT", "TYPE", "DETAIL"))
    print("-" * 72)

    for name in agents:
        try:
            config   = _load_agent_config(name)
            provider = config.get("provider", {})
            ptype    = provider.get("type", "cli")

            if ptype == "cli":
                cli_cmd = config.get("agent", {}).get("preferred_model", name)
                detail  = "cli tool: {}".format(cli_cmd)
            elif ptype == "api":
                model_id = provider.get("model_id", "?")
                api_url  = provider.get("api_base_url", "?")
                detail   = "model: {} @ {}".format(model_id, api_url)
            else:
                detail = "unknown provider type"

            print("{:<25} {:<8} {}".format(name, ptype, detail))
        except SystemExit:
            print("{:<25} {:<8} {}".format(name, "ERROR", "failed to load config"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="llm_provider.py",
        description="LLM provider abstraction — CLI and API modes for multi-agent orchestration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # info
    p_info = sub.add_parser("info", help="Show provider config for an agent.")
    p_info.add_argument("--agent", required=True, help="Agent name (e.g. codex, openai_agent)")

    # run
    p_run = sub.add_parser("run", help="Execute a task prompt via the agent's provider.")
    p_run.add_argument("--agent",   required=True, help="Agent name")
    p_run.add_argument("--prompt",  required=True, help="Task prompt text")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Print what would be done without executing")

    # list
    sub.add_parser("list", help="List all agents and their provider types.")

    args = parser.parse_args()

    dispatch = {
        "info": cmd_info,
        "run":  cmd_run,
        "list": cmd_list,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
