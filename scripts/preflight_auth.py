#!/usr/bin/env python3
"""
preflight_auth.py — sequential CLI auth warm-up before launching the worker pool.

Plan §6.2: concurrently refreshing a CLI's auth/token cache (e.g. ~/.codex/auth.json
or the agy keyring) can corrupt it. The mitigation is to force any token refresh
*once, sequentially* before the parallel pool starts, so workers then read an
already-warm cache read-only.

This runs a lightweight, side-effect-free probe per model in dry-run mode (it does
spend a minimal real CLI call budget) and exits 0 when every probe resolves. It
reads only env-var NAMES; no secrets are printed.

Usage:
    python scripts/preflight_auth.py --models codex antigravity
    python scripts/preflight_auth.py            # defaults to all cli agents
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import llm_provider as lp

ROOT         = Path(__file__).resolve().parent.parent

DEFAULT_MODELS = ["codex", "antigravity", "claude-code"]

PROBE_PROMPTS = {
    "codex": "info",
    "antigravity": "health",
    "agy": "health",
    "claude-code": "health",
}


def _pick_probe_model(provider: dict, cli_cmd: str):
    """Return a minimal model slug for CLIs that require one during preflight."""
    cli_cmd_lower = str(cli_cmd).lower()
    if "agy" not in cli_cmd_lower and "antigravity" not in cli_cmd_lower:
        return None

    if provider.get("model"):
        return provider["model"]

    mapping = provider.get("complexity_mapping", {})
    for level in ("S", "M", "L", "XL"):
        model = mapping.get(level, {}).get("model")
        if model:
            return model
    return None


def build_probe(model: str):
    """Resolve the real CLI argv/env for a provider preflight probe."""
    config = lp._load_agent_config(model)
    provider = config.get("provider", {})
    if provider.get("type", "cli") != "cli":
        raise RuntimeError("preflight only supports cli providers (agent '{}')".format(model))

    cli_cmd = lp._resolve_cli_cmd(config, model)
    exec_args = provider.get("cli_exec_args", [])
    probe_model = _pick_probe_model(provider, cli_cmd)
    prompt = PROBE_PROMPTS.get(model, "health")
    argv, env_override = lp._assemble_cli_argv(cli_cmd, exec_args, probe_model, None, prompt)
    return (argv, env_override)


def probe(model: str) -> bool:
    """Spawn the model's real CLI probe. Returns True on success.

    The probe is intentionally tiny but live: it must fail on a missing binary or
    broken auth state, and it warms any on-disk credential cache sequentially
    before the parallel worker pool starts.
    """
    argv, env_override = build_probe(model)
    subprocess_kwargs = {
        "capture_output": True,
        "text": True,
        "stdin": subprocess.DEVNULL,
    }
    if env_override is not None:
        subprocess_kwargs["env"] = env_override
    res = subprocess.run(
        argv,
        **subprocess_kwargs
    )
    ok = res.returncode == 0
    status = "OK" if ok else "FAIL (rc={})".format(res.returncode)
    print("[preflight] {:<14} {}".format(model, status))
    if not ok and res.stderr:
        print(res.stderr.strip(), file=sys.stderr)
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequential CLI auth warm-up.")
    parser.add_argument("--models", nargs="*", default=DEFAULT_MODELS,
                        help="Models to probe (default: codex antigravity claude-code)")
    args = parser.parse_args()

    all_ok = True
    for model in args.models:
        if not probe(model):
            all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
