#!/usr/bin/env python3
"""Run AGY headlessly behind a real PTY on Windows, macOS, or Linux.

The public AGY CLI currently accepts its print-mode prompt only as an argument.
This wrapper keeps the AOA-facing contract on stdin/prompt-file, but the final
AGY child argv necessarily contains the prompt until AGY gains stdin support.
"""

from __future__ import annotations

import argparse
import os
import queue
import re
import signal
import sys
import threading
import time

ERROR_PREFIX = "AGY_PTY_ERROR:"


def _error(reason: str) -> None:
    print(f"{ERROR_PREFIX} {reason}", file=sys.stderr, flush=True)


def _argv(a, prompt: str) -> list[str]:
    argv = [a.agy, "--dangerously-skip-permissions"]
    if a.model:
        argv += ["--model", a.model]
    return argv + ["--print", prompt, "--print-timeout", f"{a.timeout}s"]


def _run_windows(argv: list[str], workdir: str, timeout: int) -> tuple[int, str]:
    try:
        from winpty import PtyProcess
    except ImportError:
        _error("pywinpty_missing")
        return 2, ""
    old_cwd = os.getcwd()
    os.chdir(workdir)
    try:
        proc = PtyProcess.spawn(argv, dimensions=(40, 160))
    finally:
        os.chdir(old_cwd)
    chunks: list[str] = []
    reads: queue.Queue = queue.Queue()

    def reader() -> None:
        while True:
            try:
                reads.put(("data", proc.read()))
            except EOFError:
                reads.put(("eof", ""))
                return
            except Exception as exc:  # pragma: no cover - backend-specific
                reads.put(("error", str(exc)))
                return

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            kind, value = reads.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            if not proc.isalive():
                break
            continue
        if kind == "data":
            chunks.append(value)
        elif kind == "eof":
            break
        else:
            _error(f"pty_read:{value}")
            return 1, "".join(chunks)
    else:
        try:
            proc.terminate(force=True)
        except Exception:
            pass
        _error("timeout")
        return 124, "".join(chunks)
    status = proc.exitstatus
    return (int(status) if status not in (None, 0) else 0), "".join(chunks)


def _run_posix(argv: list[str], workdir: str, timeout: int) -> tuple[int, str]:
    import pty
    import select
    import subprocess

    master, slave = pty.openpty()
    proc = subprocess.Popen(argv, cwd=workdir, stdin=slave, stdout=slave, stderr=slave,
                            start_new_session=True, close_fds=True)
    os.close(slave)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            if proc.poll() is not None and not ready:
                break
        else:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            _error("timeout")
            return 124, b"".join(chunks).decode("utf-8", "replace")
    finally:
        os.close(master)
    return proc.wait(timeout=5), b"".join(chunks).decode("utf-8", "replace")


def _strip_terminal(value: str) -> str:
    value = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", value)
    value = re.sub(r"\x1b[\[\]][0-9;?]*[ -/]*[@-~]", "", value)
    value = re.sub(r"\x1b[@-Z\\-_]", "", value)
    return value.replace("\x07", "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--agy", default="agy")
    ap.add_argument("--model")
    a = ap.parse_args(argv)
    if a.prompt_file:
        with open(a.prompt_file, encoding="utf-8-sig") as handle:
            prompt = handle.read()
    elif a.prompt is not None:
        prompt = a.prompt
    else:
        prompt = sys.stdin.read()
    if not prompt.strip():
        _error("empty_prompt")
        return 1
    try:
        code, output = (_run_windows(_argv(a, prompt), a.workdir, a.timeout)
                        if os.name == "nt" else
                        _run_posix(_argv(a, prompt), a.workdir, a.timeout))
    except FileNotFoundError:
        _error("agy_missing")
        return 2
    clean = _strip_terminal(output)
    sys.stdout.write(clean)
    sys.stdout.flush()
    if code not in (0, 124):
        _error(f"agy_exit_{code}")
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _error(f"exception:{type(exc).__name__}:{exc}")
        raise SystemExit(1)
