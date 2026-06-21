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

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# QA-1: Import shared utilities from config_loader instead of duplicating them
import config_loader as cl

ROOT       = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config" / "agents"
DEFAULTS   = CONFIG_DIR / "_defaults.toml"
# PTME: repo-local task queue — the single source of truth for per-task
# model/effort overrides looked up via `run --task-id`. Module-level so tests
# can monkeypatch it (mirrors coordinator.py / task_router.py).
TASKS_FILE = ROOT / "tasks" / "active_tasks.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _agent_toml_path(agent_name: str) -> Path:
    """Resolve the TOML path for *agent_name*, rejecting path-traversal attempts.

    Raises SystemExit(1) on invalid names (used by CLI commands).
    """
    try:
        return cl.safe_agent_path(CONFIG_DIR, agent_name)
    except cl.ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


def _load_toml(path: Path) -> dict:
    """Load a TOML file. Returns {} if the file does not exist.

    Propagates ConfigLoadError — callers decide whether to sys.exit or print ERROR.
    """
    return cl.load_toml(path)


def _load_agent_config(agent_name: str) -> dict:
    """Return the fully-merged config for *agent_name* (defaults + agent overrides).

    Propagates ConfigLoadError so cmd_list can print an ERROR row without crashing.
    """
    defaults   = _load_toml(DEFAULTS)
    agent_file = cl.safe_agent_path(CONFIG_DIR, agent_name)
    overrides  = _load_toml(agent_file)
    return cl.deep_merge(defaults, overrides)


def _list_agent_names() -> list:
    """Return sorted list of agent names (TOML stem, excluding _defaults)."""
    return cl.list_agent_names(CONFIG_DIR)


def _is_anthropic(base_url: str) -> bool:
    """Return True when the base URL points to the Anthropic API."""
    return "api.anthropic.com" in base_url


def _resolve_cli_cmd(config: dict, agent_name: str) -> str:
    """Resolve the CLI executable name for a cli-type provider.

    Precedence: provider.cli_cmd  ->  agent.preferred_model  ->  agent_name.
    Keeping this in one helper makes cmd_info, cmd_run and cmd_list resolve the
    binary identically (PTME T-CODE-03).
    """
    provider = config.get("provider", {})
    return provider.get("cli_cmd") or config.get("agent", {}).get("preferred_model", agent_name)


def _load_task_overrides(task_id: str):
    """Return (complexity, provider_model, provider_effort) for *task_id*.

    Reads the repo-local TASKS_FILE. Any missing file / parse error / missing
    task yields (None, None, None) and a non-fatal warning — per-task overrides
    are optional context, never a hard dependency.
    """
    if not task_id:
        return (None, None, None)
    if not TASKS_FILE.exists():
        return (None, None, None)
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001 — best-effort context load
        print(
            "[WARNING] Failed to load/parse {}: {}".format(TASKS_FILE, exc),
            file=sys.stderr,
        )
        return (None, None, None)

    for task in data.get("tasks", []):
        if task.get("task_id") == task_id:
            return (
                task.get("complexity"),
                task.get("provider_model"),
                task.get("provider_effort"),
            )
    return (None, None, None)


def resolve_model_effort(
    provider: dict,
    cli_model=None,
    cli_effort=None,
    task_model=None,
    task_effort=None,
    complexity=None,
):
    """PTME selector: resolve (model, effort) for a task by 4-tier precedence.

    1. Direct CLI overrides           (cli_model / cli_effort)
    2. Per-task overrides             (task_model / task_effort from active_tasks.json)
    3. Complexity mapping             (provider.complexity_mapping[<S|M|L|XL>])
    4. Agent default                  (provider.model / provider.effort)

    Each of model and effort is resolved independently, so a task may take its
    model from one tier and its effort from another. Returns (model, effort),
    either of which may be None when nothing in the chain supplies a value
    (tier 5 — bare CLI binary, legacy behavior).
    """
    final_model = cli_model or task_model
    final_effort = cli_effort or task_effort

    mapping = provider.get("complexity_mapping", {})
    if complexity and complexity in mapping:
        mapped = mapping[complexity]
        if not final_model:
            final_model = mapped.get("model")
        if not final_effort:
            final_effort = mapped.get("effort")

    if not final_model:
        final_model = provider.get("model")
    if not final_effort:
        final_effort = provider.get("effort")

    return (final_model, final_effort)


def _assemble_cli_argv(cli_cmd, exec_args, model, effort, prompt):
    """Build the subprocess argv + optional env override for a CLI provider.

    Returns (argv, env_override). env_override is None unless the binary needs a
    modified environment (agy → TERM=xterm to prevent headless hangs). Model and
    effort flags are mapped per the confirmed CLI controls:
      * codex: -m <model>  -c model_reasoning_effort="<effort>"
      * agy:   --model <model>   (no effort flag — model slug carries the tier)
    """
    argv = [cli_cmd, *exec_args]
    cli_cmd_lower = str(cli_cmd).lower()
    env_override = None

    if "codex" in cli_cmd_lower:
        if model:
            argv.extend(["-m", model])
        if effort:
            argv.extend(["-c", 'model_reasoning_effort="{}"'.format(effort)])
    elif "agy" in cli_cmd_lower or "antigravity" in cli_cmd_lower:
        if model:
            argv.extend(["--model", model])
        env_override = os.environ.copy()
        env_override["TERM"] = "xterm"

    argv.append(prompt)
    return (argv, env_override)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_info(args) -> None:
    """Print provider configuration for a single agent."""
    agent_name = args.agent
    agent_file = _agent_toml_path(agent_name)  # exits on invalid name

    if not agent_file.exists():
        print(
            "[ERROR] No config file for agent '{}' ({}).".format(agent_name, agent_file),
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        config = _load_agent_config(agent_name)
    except cl.ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    provider = config.get("provider", {})
    ptype    = provider.get("type", "cli")

    print("Agent:         {}".format(agent_name))
    print("Provider type: {}".format(ptype))

    if ptype == "cli":
        cli_cmd = _resolve_cli_cmd(config, agent_name)
        exec_args = provider.get("cli_exec_args", [])
        print("CLI tool:      {}".format(cli_cmd))
        if exec_args:
            print("CLI exec args: {}".format(" ".join(exec_args)))

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

    agent_file = _agent_toml_path(agent_name)  # exits on invalid name

    if not agent_file.exists():
        print(
            "[ERROR] No config file for agent '{}' ({}).".format(agent_name, agent_file),
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        config = _load_agent_config(agent_name)
    except cl.ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    provider = config.get("provider", {})
    ptype    = provider.get("type", "cli")

    # ---- CLI mode ----
    if ptype == "cli":
        # cli_cmd takes precedence over the legacy preferred_model fallback.
        cli_cmd = _resolve_cli_cmd(config, agent_name)
        # Args inserted between the command and the prompt, e.g. ["exec"] so
        # Codex runs as `codex exec "<prompt>"` instead of opening its
        # interactive TUI (which hangs in automation).
        exec_args = provider.get("cli_exec_args", [])
        if not isinstance(exec_args, list):
            print(
                "[ERROR] provider.cli_exec_args must be a list for agent '{}'.".format(agent_name),
                file=sys.stderr,
            )
            sys.exit(1)

        # PTME: additive getattr reads so direct cmd_run() callers (existing
        # tests build a Namespace without these attrs) never raise AttributeError.
        arg_task_id    = getattr(args, "task_id", None)
        arg_model      = getattr(args, "model", None)
        arg_effort     = getattr(args, "effort", None)
        arg_complexity = getattr(args, "complexity", None)

        # Per-task overrides from the repo-local task queue (tier 2 + complexity).
        task_complexity, task_model, task_effort = _load_task_overrides(arg_task_id)
        resolved_complexity = arg_complexity or task_complexity

        final_model, final_effort = resolve_model_effort(
            provider,
            cli_model=arg_model,
            cli_effort=arg_effort,
            task_model=task_model,
            task_effort=task_effort,
            complexity=resolved_complexity,
        )

        argv, env_override = _assemble_cli_argv(
            cli_cmd, exec_args, final_model, final_effort, prompt
        )
        print("CLI command: {}".format(" ".join(argv[:-1]) + " \"{}\"".format(prompt)))

        if not dry_run:
            try:
                subprocess_kwargs = {
                    "capture_output": True,
                    "text": True,
                    # Close stdin: exec-style CLIs (e.g. `codex exec`) read stdin
                    # and block forever waiting on EOF when launched detached/
                    # without a TTY. DEVNULL gives an immediate EOF.
                    "stdin": subprocess.DEVNULL,
                }
                if env_override is not None:
                    subprocess_kwargs["env"] = env_override
                result = subprocess.run(argv, **subprocess_kwargs)
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

        # QA-6: read max_tokens from TOML [provider] section, default 4096
        max_tokens = int(provider.get("max_tokens", 4096))

        # REL-1: read per-provider timeout from TOML, default 60s
        timeout_seconds = int(provider.get("timeout_seconds", 60))

        if is_anth:
            endpoint = "{}/messages".format(api_base_url.rstrip("/"))
            payload  = {
                "model":      model_id,
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            }
        else:
            endpoint = "{}/chat/completions".format(api_base_url.rstrip("/"))
            payload  = {
                "model":      model_id,
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
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
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
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
            # REL-1: explicit timeout prevents indefinite hangs on unresponsive APIs
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
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
        except socket.timeout:
            # REL-1: catch socket-level timeout raised by urlopen
            print(
                "[ERROR] Request to {} timed out after {}s.".format(endpoint, timeout_seconds),
                file=sys.stderr,
            )
            sys.exit(1)
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
    """Print all agents and their provider types in a table.

    REL-5: catches ConfigLoadError per agent (instead of SystemExit) so a single
    broken TOML file never aborts the entire listing.
    """
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
                cli_cmd = _resolve_cli_cmd(config, name)
                detail  = "cli tool: {}".format(cli_cmd)
            elif ptype == "api":
                model_id = provider.get("model_id", "?")
                api_url  = provider.get("api_base_url", "?")
                detail   = "model: {} @ {}".format(model_id, api_url)
            else:
                detail = "unknown provider type"

            print("{:<25} {:<8} {}".format(name, ptype, detail))
        except cl.ConfigLoadError:
            # REL-5: ConfigLoadError is a domain exception — safe to catch per-agent
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
    # PTME per-task model + effort selection (all optional, additive).
    p_run.add_argument("--task-id", help="Task ID to load model/effort context from active_tasks.json")
    p_run.add_argument("--model",   help="Direct override for the internal model slug")
    p_run.add_argument("--effort",  help="Direct override for reasoning effort (low, medium, high, xhigh)")
    p_run.add_argument("--complexity", help="Direct override for task complexity (S, M, L, XL)")

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
