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

import argparse
import json
import os
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


AOA_ROOT = Path(__file__).resolve().parent.parent
AOA_CONFIG_PATH = AOA_ROOT / "aoa.config.json"
AOA_ENV = {
    "paths.workdir": "AOA_WORKDIR",
    "paths.agy_workers": "AOA_AGY_WORKERS_DIR",
    "paths.codex_workers": "AOA_CODEX_WORKERS_DIR",
    "paths.agy_queue": "AOA_AGY_QUEUE_DIR",
    "paths.agy_results": "AOA_AGY_RESULTS_DIR",
    "cli.python": "AOA_PYTHON_CMD",
    "cli.powershell": "AOA_POWERSHELL_CMD",
    "cli.claude": "AOA_CLAUDE_CMD",
    "cli.codex": "AOA_CODEX_CMD",
    "cli.agy": "AOA_AGY_CMD",
    "timeouts.codex_dispatch_seconds": "AOA_CODEX_TIMEOUT_SECONDS",
    "timeouts.agy_dispatch_seconds": "AOA_AGY_TIMEOUT_SECONDS",
    "timeouts.agy_invoke_seconds": "AOA_AGY_INVOKE_TIMEOUT_SECONDS",
    "timeouts.agy_usage_seconds": "AOA_AGY_USAGE_TIMEOUT_SECONDS",
    "timeouts.health_probe_seconds": "AOA_HEALTH_PROBE_TIMEOUT_SECONDS",
    "timeouts.agy_poll_seconds": "AOA_AGY_POLL_SECONDS",
    "timeouts.api_request_seconds": "AOA_API_TIMEOUT_SECONDS",
    "engine_limits.claude": "AOA_CLAUDE_MAX_PARALLEL",
    "engine_limits.codex": "AOA_CODEX_MAX_PARALLEL",
    "engine_limits.agy": "AOA_AGY_MAX_PARALLEL",
}
AOA_PATH_KEYS = {key for key in AOA_ENV if key.startswith("paths.")}
AOA_CLI_KEYS = {key for key in AOA_ENV if key.startswith("cli.")}
AOA_INT_KEYS = {
    key for key in AOA_ENV
    if key.startswith("timeouts.") or key.startswith("engine_limits.")
}

DEFAULT_MODEL_CAPABILITIES = {
    "claude-haiku-4.5": {
        "family": "claude", "model_family": "claude",
        "strengths": ["cheap fast edits", "small docs", "label/copy fixes"],
        "cost_tier": "low",
    },
    "claude-sonnet-4.6": {
        "family": "claude", "model_family": "claude",
        "strengths": ["balanced reasoning", "coordination", "documentation"],
        "cost_tier": "medium",
    },
    "claude-opus-4.8": {
        "family": "claude", "model_family": "claude",
        "strengths": ["architecture", "security review", "hard design judgment"],
        "cost_tier": "high",
    },
    "gpt-5.3-codex": {
        "family": "codex", "model_family": "gpt",
        "strengths": ["single-file coding", "fast terminal work", "contained refactors"],
        "cost_tier": "medium",
    },
    "gpt-5.5": {
        "family": "codex", "model_family": "gpt",
        "strengths": ["heavier repo surgery", "multi-file coding", "complex bugfixes"],
        "cost_tier": "high",
    },
    "gemini-3.5-flash": {
        "family": "agy", "model_family": "gemini",
        "strengths": ["cheap drafting", "fast research bursts", "broad parallel work"],
        "cost_tier": "low",
    },
    "gemini-3.1-pro": {
        "family": "agy", "model_family": "gemini",
        "strengths": ["deep research", "long context synthesis", "parallel planning"],
        "cost_tier": "high",
    },
}
DEFAULT_MODEL_LADDERS = {
    "claude": {
        "S": ["claude-haiku-4.5", "low"],
        "M": ["claude-sonnet-4.6", "medium"],
        "L": ["claude-sonnet-4.6", "high"],
        "XL": ["claude-opus-4.8", "high"],
    },
    "codex": {
        "S": ["gpt-5.3-codex", "low"],
        "M": ["gpt-5.3-codex", "medium"],
        "L": ["gpt-5.5", "high"],
        "XL": ["gpt-5.5", "high"],
    },
    "agy": {
        "S": ["gemini-3.5-flash", "low"],
        "M": ["gemini-3.5-flash", "medium"],
        "L": ["gemini-3.1-pro", "high"],
        "XL": ["gemini-3.1-pro", "high"],
    },
}


def _set_nested(data: dict, dotkey: str, value) -> None:
    cur = data
    parts = dotkey.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _portable_aoa_defaults() -> dict:
    return {
        "schema_version": 1,
        "paths": {
            "workdir": ".", "agy_workers": "workspaces/agy",
            "codex_workers": "workspaces/codex", "agy_queue": "tasks/agy_queue",
            "agy_results": "tasks/agy_results",
        },
        "cli": {
            "python": "python", "powershell": "powershell", "claude": "claude",
            "codex": "codex", "agy": "agy",
        },
        "timeouts": {
            "codex_dispatch_seconds": 600, "agy_dispatch_seconds": 900,
            "agy_invoke_seconds": 300, "agy_usage_seconds": 18,
            "health_probe_seconds": 90, "agy_poll_seconds": 4,
            "api_request_seconds": 60,
        },
        "engine_limits": {"claude": 1, "codex": 3, "agy": 3},
        "models": {
            "capabilities": DEFAULT_MODEL_CAPABILITIES,
            "ladders": DEFAULT_MODEL_LADDERS,
        },
    }


def _validate_aoa_structure(data: dict) -> None:
    for section in ("paths", "cli", "timeouts", "engine_limits", "models"):
        if not isinstance(data.get(section), dict):
            raise ConfigLoadError("[ERROR] {} must be a JSON object".format(section))
    for key in AOA_CLI_KEYS:
        value = get_nested(data, key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigLoadError("[ERROR] {} must be a non-empty command string".format(key))

    capabilities = get_nested(data, "models.capabilities")
    ladders = get_nested(data, "models.ladders")
    if not isinstance(capabilities, dict):
        raise ConfigLoadError("[ERROR] models.capabilities must be a JSON object")
    if not isinstance(ladders, dict):
        raise ConfigLoadError("[ERROR] models.ladders must be a JSON object")
    for model, info in capabilities.items():
        if not isinstance(model, str) or not isinstance(info, dict):
            raise ConfigLoadError("[ERROR] each models.capabilities entry must be an object")
        if not isinstance(info.get("family"), str) or not isinstance(info.get("model_family"), str):
            raise ConfigLoadError(
                "[ERROR] models.capabilities.{} requires string family and model_family".format(model)
            )
    for engine, ladder in ladders.items():
        if not isinstance(engine, str) or not isinstance(ladder, dict):
            raise ConfigLoadError("[ERROR] each models.ladders entry must be an object")
        for complexity, choice in ladder.items():
            if (
                complexity not in ("S", "M", "L", "XL")
                or not isinstance(choice, list)
                or len(choice) != 2
                or not all(isinstance(item, str) and item for item in choice)
            ):
                raise ConfigLoadError(
                    "[ERROR] models.ladders.{}.{} must be [model, effort]".format(
                        engine, complexity
                    )
                )
            if choice[0] not in capabilities:
                raise ConfigLoadError(
                    "[ERROR] models.ladders.{}.{} references unknown model {}".format(
                        engine, complexity, choice[0]
                    )
                )


def load_aoa_config(config_path: Path | None = None, environ: dict | None = None) -> dict:
    """Load AOA settings using environment > JSON file > portable defaults.

    Relative paths always resolve against the repository/install root, never cwd.
    AOA_CONFIG may select another JSON file; its relative values still resolve
    against the install root so moving the caller's cwd cannot change behavior.
    """
    env = os.environ if environ is None else environ
    selected = config_path or Path(env.get("AOA_CONFIG", AOA_CONFIG_PATH))
    data = _portable_aoa_defaults()
    if selected.exists():
        try:
            parsed = json.loads(selected.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ConfigLoadError("[ERROR] Failed to parse {}: {}".format(selected, exc)) from exc
        if not isinstance(parsed, dict) or parsed.get("schema_version") != 1:
            raise ConfigLoadError("[ERROR] {} must be an object with schema_version 1".format(selected))
        data = deep_merge(data, parsed)
    elif config_path is not None or env.get("AOA_CONFIG"):
        raise ConfigLoadError("[ERROR] AOA config file not found: {}".format(selected))

    for key, env_name in AOA_ENV.items():
        raw = env.get(env_name)
        if raw is None or raw == "":
            continue
        if key in AOA_INT_KEYS:
            try:
                raw = int(raw)
            except ValueError as exc:
                raise ConfigLoadError("[ERROR] {} must be a positive integer".format(env_name)) from exc
        _set_nested(data, key, raw)

    _validate_aoa_structure(data)
    for key in AOA_INT_KEYS:
        value = get_nested(data, key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ConfigLoadError("[ERROR] {} must be a positive integer".format(key))
    for key in AOA_PATH_KEYS:
        value = get_nested(data, key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigLoadError("[ERROR] {} must be a non-empty path string".format(key))
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = AOA_ROOT / path
        _set_nested(data, key, str(path.resolve()))
    for key in AOA_CLI_KEYS:
        value = get_nested(data, key)
        command_path = Path(value).expanduser()
        if ("/" in value or "\\" in value) and not command_path.is_absolute():
            _set_nested(data, key, str((AOA_ROOT / command_path).resolve()))
    return data


def get_aoa_value(dotkey: str, config_path: Path | None = None, environ: dict | None = None):
    value = get_nested(load_aoa_config(config_path=config_path, environ=environ), dotkey)
    if value is None:
        raise ConfigLoadError("[ERROR] Unknown AOA config key: {}".format(dotkey))
    return value


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Load portable AOA configuration")
    sub = parser.add_subparsers(dest="command", required=True)
    get_parser = sub.add_parser("aoa-get", help="Print a resolved AOA setting")
    get_parser.add_argument("key")
    sub.add_parser("aoa-show", help="Print the resolved AOA configuration")
    args = parser.parse_args()
    try:
        if args.command == "aoa-get":
            value = get_aoa_value(args.key)
            print(json.dumps(value) if isinstance(value, (dict, list)) else value)
        else:
            print(json.dumps(load_aoa_config(), indent=2))
    except ConfigLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
