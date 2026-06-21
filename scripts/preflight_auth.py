#!/usr/bin/env python3
"""
preflight_auth.py — sequential CLI auth warm-up before launching the worker pool.

Plan §6.2: concurrently refreshing a CLI's auth/token cache (e.g. ~/.codex/auth.json
or the agy keyring) can corrupt it. The mitigation is to force any token refresh
*once, sequentially* before the parallel pool starts, so workers then read an
already-warm cache read-only.

This runs a lightweight, side-effect-free probe per model in dry-run mode (it does
NOT spend real task budget) and exits 0 when every probe resolves. It reads only
env-var NAMES; no secrets are printed.

Usage:
    python scripts/preflight_auth.py --models codex antigravity
    python scripts/preflight_auth.py            # defaults to all cli agents
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT         = Path(__file__).resolve().parent.parent
LLM_PROVIDER = Path(__file__).resolve().parent / "llm_provider.py"

DEFAULT_MODELS = ["codex", "antigravity", "claude-code"]


def probe(model: str) -> bool:
    """Resolve the model's provider config via a dry-run. Returns True on success.

    A dry-run forces config + CLI-binary resolution (and, for live CLIs invoked by
    operators, primes the auth cache) without executing a real task or needing an
    API key. Non-zero exit means the agent is misconfigured — fix before the pool.
    """
    res = subprocess.run(
        [sys.executable, str(LLM_PROVIDER), "run",
         "--agent", model, "--prompt", "preflight health probe", "--dry-run"],
        capture_output=True, text=True,
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
