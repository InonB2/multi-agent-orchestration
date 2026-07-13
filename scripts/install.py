#!/usr/bin/env python3
"""Shared, cross-platform installer core for AOA."""

from __future__ import print_function

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path


MIN_PYTHON = (3, 8)
EMPTY_FEEDS = (
    "agent_activity.json", "agent_activity.js", "engine_usage.json",
    "engine_usage.js", "live_tasks.json", "live_tasks.js",
    "orchestrator_stats.json", "orchestrator_stats.js", "analytics_data.js",
)


def fail(message, code=1):
    print("[ERROR] {}".format(message), file=sys.stderr)
    return code


def run(command, cwd, dry_run=False, env=None):
    print("+ {}".format(" ".join(str(part) for part in command)))
    if dry_run:
        return 0
    return subprocess.run(command, cwd=str(cwd), env=env).returncode


def load_config(root):
    local = root / "aoa.config.local.json"
    path = local if local.exists() else root / "aoa.config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("cannot read {}: {}".format(path, exc))
    if data.get("schema_version") != 1:
        raise RuntimeError("aoa.config.json must use schema_version 1")
    return data


def enabled_adapters(config):
    adapters = config.get("dispatch", {}).get("adapters", {})
    return {
        name: entry for name, entry in adapters.items()
        if isinstance(entry, dict) and entry.get("enabled", True)
    }


def discover(config):
    found = {}
    missing = []
    for engine, entry in sorted(enabled_adapters(config).items()):
        key = str(entry.get("cli_key") or engine)
        configured = str(config.get("cli", {}).get(key) or key)
        resolved = configured if os.path.isabs(configured) and Path(configured).is_file() else shutil.which(configured)
        if resolved:
            found[engine] = str(Path(resolved).resolve())
            print("[OK] adapter {:<12} {}".format(engine, found[engine]))
        else:
            missing.append(engine)
            print("[MISSING] adapter {:<9} command '{}'".format(engine, configured))
    return found, missing


def choose_engines(config, found, requested, non_interactive):
    available = sorted(found)
    if requested:
        chosen = [item.strip() for item in requested.split(",") if item.strip()]
        unknown = [item for item in chosen if item not in found]
        if unknown:
            raise RuntimeError("selected adapters are unavailable: {}".format(", ".join(unknown)))
    elif non_interactive:
        chosen = available
    else:
        answer = input("Adapters to configure [{}]: ".format(",".join(available))).strip()
        chosen = [item.strip() for item in answer.split(",") if item.strip()] if answer else available
        unknown = [item for item in chosen if item not in found]
        if unknown:
            raise RuntimeError("selected adapters are unavailable: {}".format(", ".join(unknown)))
    if len(chosen) < 2:
        raise RuntimeError("AOA demo requires at least two available adapters (worker != tester)")
    return chosen


def write_config(root, config, found, chosen, dry_run):
    adapters = config["dispatch"]["adapters"]
    for engine, entry in adapters.items():
        entry["enabled"] = engine in chosen
        if engine in found:
            key = str(entry.get("cli_key") or engine)
            config.setdefault("cli", {})[key] = found[engine]
    prior = config.get("demo", {})
    worker = prior.get("worker_engine") if prior.get("worker_engine") in chosen else chosen[0]
    preferred_tester = prior.get("tester_engine")
    tester = (
        preferred_tester
        if preferred_tester in chosen and preferred_tester != worker
        else next(engine for engine in chosen if engine != worker)
    )
    config["demo"] = {"worker_engine": worker, "tester_engine": tester}
    path = root / "aoa.config.local.json"
    if not dry_run:
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print("[OK] config {} (demo: {} -> {})".format(path, worker, tester))


def seed_state(root, dry_run):
    example = root / "tasks" / "active_tasks.example.json"
    target = root / "tasks" / "active_tasks.json"
    runtime_dashboard = root / ".aoa" / "dashboard"
    if not example.is_file():
        raise RuntimeError("clean task seed is missing: {}".format(example))
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(str(example), str(target))
        runtime_dashboard.mkdir(parents=True, exist_ok=True)
        for source in (root / "dashboard").iterdir():
            destination = runtime_dashboard / source.name
            if source.is_file() and not destination.exists():
                shutil.copy2(str(source), str(runtime_dashboard / source.name))
    print("[OK] task seed {}{}".format(target, " (preserved)" if target.exists() else ""))
    print("[OK] clean runtime dashboard {}".format(runtime_dashboard))


def auth_probe(root, chosen, dry_run, skip_auth):
    if skip_auth:
        print("[WARN] authentication probes skipped by explicit option")
        return 0
    command = [sys.executable, str(root / "scripts" / "preflight_auth.py"), "--engines"] + chosen
    env = os.environ.copy()
    env["AOA_CONFIG"] = str(root / "aoa.config.local.json")
    return run(command, root, dry_run, env)


def port_open(port):
    sock = socket.socket()
    sock.settimeout(0.2)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def open_dashboard(root, dry_run, no_open):
    if no_open:
        print(
            "[OK] dashboard launch skipped; run: {} scripts/dashboard_server.py "
            "--directory .aoa/dashboard".format(sys.executable)
        )
        return
    url = "http://127.0.0.1:7780/"
    command = [sys.executable, str(root / "scripts" / "dashboard_server.py"),
               "--directory", str(root / ".aoa" / "dashboard")]
    print("+ {}".format(" ".join(command)))
    if dry_run:
        return
    if not port_open(7780):
        log_path = root / ".aoa" / "dashboard_server.log"
        log = log_path.open("a", encoding="utf-8")
        kwargs = {"cwd": str(root), "stdout": log, "stderr": subprocess.STDOUT}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
        for _ in range(30):
            if port_open(7780):
                break
            time.sleep(0.1)
        if not port_open(7780):
            raise RuntimeError("dashboard server did not start; inspect {}".format(log_path))
    webbrowser.open(url)
    print("[OK] dashboard {}".format(url))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--engines", help="comma-separated discovered adapters to enable")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--skip-auth", action="store_true", help="test/troubleshooting only")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if sys.version_info < MIN_PYTHON:
        return fail("Python 3.8 or newer is required; found {}.{}.{}".format(*sys.version_info[:3]), 2)
    root = Path(args.root).resolve()
    if not (root / "pyproject.toml").is_file() or not (root / "scripts" / "dispatch.py").is_file():
        return fail("installer must run from the reconciled AOA public-repo checkout", 2)
    try:
        config = load_config(root)
        found, missing = discover(config)
        chosen = choose_engines(config, found, args.engines, args.non_interactive)
        write_config(root, config, found, chosen, args.dry_run)
        seed_state(root, args.dry_run)
        if run([sys.executable, "-m", "pip", "install", "-e", str(root)], root, args.dry_run):
            return fail("editable package installation failed", 3)
        if auth_probe(root, chosen, args.dry_run, args.skip_auth):
            return fail("one or more configured adapter authentication probes failed", 4)
        open_dashboard(root, args.dry_run, args.no_open)
    except (RuntimeError, OSError, ValueError) as exc:
        return fail(str(exc), 2)
    if missing:
        print("[INFO] unavailable adapters left disabled: {}".format(", ".join(missing)))
    launcher = shutil.which("aoa")
    if launcher:
        print("[DONE] Run: aoa demo")
    else:
        print("[WARN] the Python scripts directory is not on PATH")
        print("[DONE] Run: {} -m aoa_cli demo".format(sys.executable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
