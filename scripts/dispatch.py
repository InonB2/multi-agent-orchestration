#!/usr/bin/env python3
"""Cross-platform, agent-agnostic AOA dispatch entry point."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import config_loader
import agent_telemetry
from adapters.base import DispatchRequest, Invocation

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent

EXIT_MISSING = 2
EXIT_EMPTY = 3
EXIT_TIMEOUT = 124


@dataclass
class DispatchResult:
    engine: str
    status: str
    exit_code: int
    message: str
    stdout: str
    stderr: str
    elapsed_seconds: float


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_event(path: Path, event: str, request: DispatchRequest, task_id: str,
                 role: str, elapsed: float | None = None, exit_code: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": 1, "timestamp": _now(), "event": event,
        "engine": request.engine, "adapter": request.engine,
        "task_id": task_id, "role": role,
    }
    if elapsed is not None:
        record["elapsed_seconds"] = round(elapsed, 3)
    if exit_code is not None:
        record["exit_code"] = exit_code
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _dashboard_event(event: str, request: DispatchRequest, task_id: str, role: str,
                     status: str | None = None) -> None:
    """Bridge common dispatch lifecycle into the existing dashboard feed."""
    namespace = argparse.Namespace(
        agent=f"{request.engine}-{role}", task=task_id,
        desc=f"AOA dispatch {task_id}", model=request.model, effort=request.effort,
        role=role, reason="common adapter dispatch", status=status,
    )
    if event == "start":
        agent_telemetry.cmd_start(namespace)
    else:
        agent_telemetry.cmd_stop(namespace)


def _safe_lifecycle(path: Path, event: str, request: DispatchRequest, task_id: str,
                    role: str, dashboard: bool, elapsed: float | None = None,
                    exit_code: int | None = None) -> None:
    try:
        _write_event(path, event, request, task_id, role, elapsed, exit_code)
    except Exception:
        pass
    if dashboard:
        try:
            _dashboard_event("start" if event == "start" else "stop", request,
                             task_id, role, event)
        except Exception:
            pass


def _load_adapter(config: dict, engine: str):
    adapters = config.get("dispatch", {}).get("adapters", {})
    entry = adapters.get(engine)
    if not isinstance(entry, dict) or not entry.get("module"):
        raise ValueError(f"No dispatch adapter configured for engine '{engine}'")
    module = importlib.import_module(str(entry["module"]))
    adapter = module.Adapter()
    if adapter.engine != engine:
        raise ValueError(f"Adapter '{entry['module']}' declares engine '{adapter.engine}', expected '{engine}'")
    return adapter, entry


def _configured_executable(config: dict, engine: str, entry: dict) -> str:
    key = str(entry.get("cli_key") or engine)
    return str(config.get("cli", {}).get(key) or key)


def _resolve_executable(config: dict, engine: str, entry: dict) -> str:
    executable = _configured_executable(config, engine, entry)
    if os.path.isabs(executable):
        return executable if Path(executable).exists() else ""
    return shutil.which(executable) or ""


def _terminate_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _invoke(invocation: Invocation, workdir: Path, timeout: int) -> tuple[int, str, str, bool]:
    kwargs = {
        "args": invocation.argv, "cwd": str(workdir), "env": invocation.env,
        "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "text": True, "encoding": "utf-8", "errors": "replace",
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(**kwargs)
    try:
        stdout, stderr = proc.communicate(invocation.stdin, timeout=timeout)
        return proc.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        _terminate_tree(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", "Timed out draining terminated process pipes"
        return EXIT_TIMEOUT, stdout, stderr, True
    except KeyboardInterrupt:
        _terminate_tree(proc)
        raise


def dispatch_request(request: DispatchRequest, *, task_id: str = "ad-hoc", role: str = "worker",
                     dry_run: bool = False, preflight: bool = True,
                     telemetry_path: Path | None = None, config: dict | None = None,
                     emit_telemetry: bool = True) -> DispatchResult:
    config = config if config is not None else config_loader.load_aoa_config()
    adapter, entry = _load_adapter(config, request.engine)
    configured_executable = request.executable or _configured_executable(config, request.engine, entry)
    if dry_run:
        executable = configured_executable
    elif request.executable:
        executable = (request.executable if os.path.isabs(request.executable) and Path(request.executable).exists()
                      else shutil.which(request.executable) or "")
    else:
        executable = _resolve_executable(config, request.engine, entry)
    if not dry_run and not executable:
        return DispatchResult(request.engine, "missing_executable", EXIT_MISSING, "", "",
                              f"Executable for '{request.engine}' not found", 0.0)
    invocation = adapter.build(request, executable, SCRIPTS)
    if dry_run:
        redacted_command = []
        for value in invocation.display_argv:
            if value == executable:
                redacted_command.append(f"<{request.engine}-cli>")
            elif value == sys.executable:
                redacted_command.append("<python>")
            else:
                redacted_command.append(value)
        plan = {
            "engine": request.engine, "adapter": entry["module"],
            "command": redacted_command, "stdin": "<redacted>",
            "workdir": "<workdir>", "timeout": request.timeout,
            "preflight": preflight,
        }
        return DispatchResult(request.engine, "dry_run", 0, json.dumps(plan), "", "", 0.0)

    if preflight:
        probe_text, expected = adapter.probe_prompt()
        probe_request = DispatchRequest(request.engine, probe_text, request.workdir,
                                        min(request.timeout, int(config.get("timeouts", {}).get("health_probe_seconds", 90))),
                                        request.model, request.effort, request.enable_mcp,
                                        request.executable)
        probe_invocation = adapter.build(probe_request, executable, SCRIPTS)
        code, out, err, timed_out = _invoke(probe_invocation, request.workdir, probe_request.timeout)
        parsed = adapter.parse(out, err)
        if timed_out or code != 0 or expected not in parsed:
            return DispatchResult(request.engine, "preflight_failed", EXIT_MISSING, "", out,
                                  err or f"Authentication/health probe failed for '{request.engine}'", 0.0)

    telemetry_path = telemetry_path or (ROOT / str(config.get("dispatch", {}).get(
        "telemetry_path", "logs/dispatch_telemetry.jsonl")))
    dashboard_enabled = bool(config.get("dispatch", {}).get("dashboard_telemetry", False))
    started = time.monotonic()
    if emit_telemetry:
        _safe_lifecycle(telemetry_path, "start", request, task_id, role, dashboard_enabled)
    stdout = stderr = message = ""
    status, code = "failure", 1
    try:
        code, stdout, stderr, timed_out = _invoke(invocation, request.workdir, request.timeout)
        message = adapter.parse(stdout, stderr)
        if timed_out:
            status, code = "timeout", EXIT_TIMEOUT
        elif code != 0:
            status = "failure"
        elif not message.strip():
            status, code = "failure", EXIT_EMPTY
            stderr = (stderr + "\nAgent returned no report").strip()
        else:
            status = "success"
    except KeyboardInterrupt:
        status, code, stderr = "cancelled", 130, "Dispatch cancelled"
    except OSError as exc:
        status, code, stderr = "failure", 1, str(exc)
    elapsed = time.monotonic() - started
    if emit_telemetry:
        _safe_lifecycle(telemetry_path, status, request, task_id, role,
                        dashboard_enabled, elapsed, code)
    return DispatchResult(request.engine, status, code, message, stdout, stderr, elapsed)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--engine", help="Configured adapter name")
    p.add_argument("--list-adapters", action="store_true")
    source = p.add_mutually_exclusive_group()
    source.add_argument("--prompt")
    source.add_argument("--prompt-file")
    p.add_argument("--workdir")
    p.add_argument("--timeout", type=int)
    p.add_argument("--task-id", default="ad-hoc")
    p.add_argument("--role", default="worker")
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--output")
    p.add_argument("--telemetry-path")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-preflight", action="store_true")
    p.add_argument("--enable-mcp", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = config_loader.load_aoa_config()
    if args.list_adapters:
        for name, entry in sorted(config.get("dispatch", {}).get("adapters", {}).items()):
            print(f"{name}\t{entry.get('module')}\t{'enabled' if entry.get('enabled', True) else 'example'}")
        return 0
    if not args.engine:
        print("[ERROR] --engine is required", file=sys.stderr)
        return 1
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8-sig")
        except OSError as exc:
            print(f"[ERROR] Cannot read prompt file: {exc}", file=sys.stderr)
            return 1
    elif args.prompt is not None:
        prompt = args.prompt
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        print("[ERROR] Provide --prompt, --prompt-file, or stdin", file=sys.stderr)
        return 1
    if not prompt.strip():
        print("[ERROR] Prompt is empty", file=sys.stderr)
        return 1
    workdir = Path(args.workdir or config.get("paths", {}).get("workdir") or ROOT).resolve()
    if not workdir.is_dir():
        print(f"[ERROR] Workdir not found: {workdir}", file=sys.stderr)
        return 1
    entry = config.get("dispatch", {}).get("adapters", {}).get(args.engine, {})
    default_timeout = entry.get("timeout_seconds") or config.get("timeouts", {}).get(
        f"{args.engine}_dispatch_seconds", 600)
    request = DispatchRequest(args.engine, prompt, workdir, args.timeout or int(default_timeout),
                              args.model, args.effort, args.enable_mcp)
    try:
        result = dispatch_request(request, task_id=args.task_id, role=args.role,
                                  dry_run=args.dry_run, preflight=not args.skip_preflight,
                                  telemetry_path=Path(args.telemetry_path) if args.telemetry_path else None,
                                  config=config)
    except (ValueError, ImportError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if result.message:
        print(result.message)
        if args.output and not args.dry_run:
            Path(args.output).write_text(result.message, encoding="utf-8")
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
