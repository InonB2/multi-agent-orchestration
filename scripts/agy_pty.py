#!/usr/bin/env python3
"""agy_pty.py - run agy headlessly by giving it a real ConPTY (pseudo-terminal).

agy --print silently drops stdout under a non-TTY (known bug: antigravity-cli #76,
gemini-cli #27466). Claude Code's tools have no TTY, so agy no-ops. This wrapper
allocates a ConPTY via pywinpty, runs `agy --print <prompt>` inside it, and captures
the output the terminal sees. Prompt is read from --prompt or stdin/file.

Usage:
    python scripts/agy_pty.py --prompt "Reply with exactly: AGY_OK"
    python scripts/agy_pty.py --prompt-file spec.md --workdir . --timeout 600
"""
import argparse
import os
import shutil
import sys
import time

from config_loader import load_aoa_config

AOA_CONFIG = load_aoa_config()
AGY = AOA_CONFIG["cli"]["agy"]
DEFAULT_WORKDIR = AOA_CONFIG["paths"]["workdir"]
DEFAULT_TIMEOUT = AOA_CONFIG["timeouts"]["agy_dispatch_seconds"]
ERROR_PREFIX = "AGY_PTY_ERROR:"


def _error(reason):
    """Emit the stable failure contract consumed by dispatch_agy.ps1."""
    sys.stderr.write(f"{ERROR_PREFIX} {reason}\n")
    sys.stderr.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("--workdir", default=DEFAULT_WORKDIR)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--agy", default=AGY)
    ap.add_argument("--model", default=None,
                    help="Force a specific agy model slug (e.g. a Claude/GPT-OSS model, "
                         "not just the Gemini default). Passed through as `--model <slug>`.")
    a = ap.parse_args()

    if not (os.path.isfile(a.agy) or shutil.which(a.agy)):
        _error(f"agy_executable_not_found:{a.agy}")
        return 2
    if a.timeout <= 0:
        _error("timeout_must_be_positive")
        return 2

    if a.prompt_file:
        with open(a.prompt_file, encoding="utf-8") as f:
            prompt = f.read()
    elif a.prompt:
        prompt = a.prompt
    else:
        prompt = sys.stdin.read()

    try:
        from winpty import PtyProcess
    except ImportError:
        _error("pywinpty_missing")
        return 2

    os.environ["TERM"] = "xterm"
    os.chdir(a.workdir)

    # spawn agy inside a real pseudo-terminal
    argv = [a.agy, "--dangerously-skip-permissions"]
    if a.model:
        # AGY's CLI can run non-Gemini families (Claude, GPT-OSS) when its plan exposes
        # them; the family gate lives in scripts/ptme.py (ENGINE_CAN_RUN["agy"]).
        argv += ["--model", a.model]
    argv += ["--print", prompt, "--print-timeout", f"{a.timeout}s"]
    proc = PtyProcess.spawn(argv, dimensions=(40, 160))

    chunks = []
    deadline = time.time() + a.timeout
    grace_deadline = deadline + 45
    timed_out = False
    while True:
        now = time.time()
        if now > grace_deadline:
            try:
                proc.terminate(force=True)
            except Exception:
                pass
            _error("timeout")
            timed_out = True
            break
        try:
            data = proc.read()  # str; EOFError at end
        except EOFError:
            break
        if data:
            chunks.append(data)
        else:
            if not proc.isalive():
                break
            time.sleep(0.05)

    out = "".join(chunks)
    out = _strip_terminal(out)
    # Force UTF-8 stdout: agy output routinely contains non-cp1252 chars (arrows →,
    # emoji, Hebrew) that crash the default Windows codec. Reconfigure, else replace.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.write(out)
    except Exception:
        sys.stdout.buffer.write(out.encode("utf-8", errors="replace"))
    sys.stdout.flush()
    if timed_out:
        return 124

    # pywinpty exposes the child status after EOF. Propagate it so callers never
    # mistake an agy CLI failure (even one with non-empty error prose) for success.
    child_exit = proc.exitstatus
    if child_exit not in (None, 0):
        _error(f"agy_exit_{child_exit}")
        return int(child_exit)
    return 0


def _strip_terminal(s):
    import re
    s = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", s)   # OSC sequences
    s = re.sub(r"\x1b[\[\]][0-9;?]*[ -/]*[@-~]", "", s)        # CSI sequences
    s = re.sub(r"\x1b[@-Z\\-_]", "", s)                          # other escapes
    s = s.replace("\x07", "")
    return s


if __name__ == "__main__":
    try:
        code = main()
    except SystemExit as exc:
        # argparse uses SystemExit(2) for invalid CLI input.
        code = int(exc.code or 0)
        if code:
            _error(f"argument_error_exit_{code}")
    except Exception as exc:
        _error(f"exception:{type(exc).__name__}:{exc}")
        code = 1
    sys.exit(code)
