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
import os
import shutil
import sys
from pathlib import Path

import llm_provider as lp
import dispatch as aoa_dispatch
from adapters.base import DispatchRequest

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


def build_probe(model: str) -> DispatchRequest:
    """Resolve a probe through the same adapter contract used for real work."""
    config = lp._load_agent_config(model)
    provider = config.get("provider", {})
    if provider.get("type", "cli") != "cli":
        raise RuntimeError("preflight only supports cli providers (agent '{}')".format(model))

    cli_cmd = lp._resolve_cli_cmd(config, model)
    probe_model = _pick_probe_model(provider, cli_cmd)
    prompt = PROBE_PROMPTS.get(model, "health")
    engine = lp._dispatch_engine(config, model, cli_cmd)
    timeout = int(lp.AOA_CONFIG.get("timeouts", {}).get("health_probe_seconds", 90))
    workdir = Path(lp.AOA_CONFIG.get("paths", {}).get("workdir") or ROOT).resolve()
    return DispatchRequest(engine, prompt, workdir, timeout, model=probe_model,
                           executable=cli_cmd)


def probe(model: str) -> bool:
    """Spawn the model's real CLI probe. Returns True on success.

    The probe is intentionally tiny but live: it must fail on a missing binary or
    broken auth state, and it warms any on-disk credential cache sequentially
    before the parallel worker pool starts.
    """
    request = build_probe(model)
    res = aoa_dispatch.dispatch_request(request, preflight=False,
                                        emit_telemetry=False, config=lp.AOA_CONFIG)
    ok = res.exit_code == 0
    status = "OK" if ok else "FAIL (rc={})".format(res.exit_code)
    print("[preflight] {:<14} {}".format(model, status))
    if not ok and res.stderr:
        print(res.stderr.strip(), file=sys.stderr)
    return ok


def probe_engine(engine: str, config: dict | None = None) -> bool:
    """Probe one registered adapter using its own health contract."""
    config = config if config is not None else lp.AOA_CONFIG
    adapters = config.get("dispatch", {}).get("adapters", {})
    entry = adapters.get(engine)
    if not isinstance(entry, dict) or not entry.get("module"):
        print("[preflight] {:<14} FAIL (not registered)".format(engine), file=sys.stderr)
        return False
    try:
        adapter, _entry = aoa_dispatch._load_adapter(config, engine)
    except (ImportError, ValueError) as exc:
        print("[preflight] {:<14} FAIL ({})".format(engine, exc), file=sys.stderr)
        return False
    key = str(entry.get("cli_key") or engine)
    command = str(config.get("cli", {}).get(key) or key)
    executable = command if os.path.isabs(command) and Path(command).is_file() else shutil.which(command)
    if not executable:
        print("[preflight] {:<14} FAIL (executable not found)".format(engine), file=sys.stderr)
        return False
    prompt, expected = adapter.probe_prompt()
    timeout = int(config.get("timeouts", {}).get("health_probe_seconds", 90))
    workdir = Path(config.get("paths", {}).get("workdir") or ROOT).resolve()
    request = DispatchRequest(engine, prompt, workdir, timeout, executable=str(executable))
    result = aoa_dispatch.dispatch_request(
        request, preflight=False, emit_telemetry=False, config=config
    )
    ok = result.exit_code == 0 and expected in result.message
    print("[preflight] {:<14} {}".format(engine, "OK" if ok else "FAIL"))
    if not ok:
        detail = result.stderr or result.message or "expected probe reply was not returned"
        print(detail.strip(), file=sys.stderr)
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequential CLI auth warm-up.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--models", nargs="*",
                        help="Models to probe (default: codex antigravity claude-code)")
    group.add_argument("--engines", nargs="*",
                       help="Registered adapter engines to probe generically")
    args = parser.parse_args()

    all_ok = True
    if args.engines is not None:
        config = lp.AOA_CONFIG
        engines = args.engines or [
            name for name, entry in config.get("dispatch", {}).get("adapters", {}).items()
            if isinstance(entry, dict) and entry.get("enabled", True)
        ]
        for engine in engines:
            if not probe_engine(engine, config):
                all_ok = False
    else:
        for model in (args.models if args.models is not None else DEFAULT_MODELS):
            if not probe(model):
                all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
