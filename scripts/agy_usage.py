#!/usr/bin/env python3
"""Capture AGY's interactive /usage screen via ConPTY and parse real quota data."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from config_loader import load_aoa_config

AOA_CONFIG = load_aoa_config()
AGY = AOA_CONFIG["cli"]["agy"]
DEFAULT_WORKDIR = AOA_CONFIG["paths"]["workdir"]
DEFAULT_TIMEOUT = AOA_CONFIG["timeouts"]["agy_usage_seconds"]


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _error_result(message: str, raw: str = "", account: str | None = None) -> Dict[str, Any]:
    return {
        "gemini": {"weekly_pct": None, "weekly_refresh": None, "five_hour_pct": None},
        "claude_gpt": {"weekly_pct": None, "weekly_refresh": None, "five_hour_pct": None},
        "account": account,
        "source": "agy /usage (conpty)",
        "confidence": "none",
        "captured_at": _iso_now(),
        "error": message,
        "raw_excerpt": raw[-1200:] if raw else "",
    }


def _strip_terminal(text: str) -> str:
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b[\[\]][0-9;?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[@-Z\\-_]", "", text)
    text = text.replace("\x07", "")
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _extract_group(screen: str, header: str) -> str:
    start = screen.find(header)
    if start == -1:
        return ""
    start += len(header)
    remainder = screen[start:]
    headings = ["GEMINI MODELS", "CLAUDE AND GPT MODELS"]
    next_positions = []
    for candidate in headings:
        if candidate == header:
            continue
        pos = remainder.find(candidate)
        if pos != -1:
            next_positions.append(pos)
    end = min(next_positions) if next_positions else len(remainder)
    return remainder[:end]


def _parse_group(section: str) -> Dict[str, Any] | None:
    if not section:
        return None
    weekly_block_match = re.search(r"Weekly Limit(?P<block>.*?)(?:Five Hour Limit|$)", section, flags=re.I | re.S)
    five_hour_block_match = re.search(r"Five Hour Limit(?P<block>.*)$", section, flags=re.I | re.S)
    weekly_block = weekly_block_match.group("block") if weekly_block_match else section
    five_hour_block = five_hour_block_match.group("block") if five_hour_block_match else section

    weekly_match = re.search(r"([0-9]+(?:\.[0-9]+)?)%\s*(?:remaining)?", weekly_block, flags=re.I)
    refresh_match = re.search(r"Refreshes in\s+([0-9]+h\s+[0-9]+m)", weekly_block, flags=re.I)
    if not refresh_match:
        refresh_match = re.search(r"Refreshes in\s+([0-9]+h\s+[0-9]+m)", section, flags=re.I)
    five_hour_match = re.search(r"([0-9]+(?:\.[0-9]+)?)%\s*(?:remaining)?", five_hour_block, flags=re.I)
    if not weekly_match or not five_hour_match:
        return None
    return {
        "weekly_pct": float(weekly_match.group(1)),
        "weekly_refresh": refresh_match.group(1) if refresh_match else None,
        "five_hour_pct": float(five_hour_match.group(1)),
    }


def parse_usage_screen(screen: str) -> Dict[str, Any]:
    cleaned = _strip_terminal(screen)
    gemini = _parse_group(_extract_group(cleaned, "GEMINI MODELS"))
    claude_gpt = _parse_group(_extract_group(cleaned, "CLAUDE AND GPT MODELS"))
    account_match = re.search(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", cleaned, flags=re.I)
    account = account_match.group(0) if account_match else None
    if gemini and claude_gpt:
        return {
            "gemini": gemini,
            "claude_gpt": claude_gpt,
            "account": account,
            "source": "agy /usage (conpty)",
            "confidence": "real",
            "captured_at": _iso_now(),
        }
    return _error_result("could not parse Models & Quota screen", cleaned, account=account)


def _spawn_pty(agy: str):
    try:
        from winpty import PtyProcess
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("PYWINPTY_MISSING: pip install pywinpty") from exc
    return PtyProcess.spawn([agy, "--dangerously-skip-permissions"], dimensions=(45, 180))


def _write_slow(proc, text: str, delay: float = 0.03) -> None:
    for ch in text:
        proc.write(ch)
        time.sleep(delay)


def _drive_usage_command(proc, *, accept_with_tab: bool) -> None:
    proc.write("\x1b")
    time.sleep(0.15)
    proc.write("/")
    time.sleep(0.18)
    _write_slow(proc, "usage", delay=0.06)
    time.sleep(0.18)
    if accept_with_tab:
        proc.write("\t")
        time.sleep(0.18)
    proc.write("\r")


def capture_usage(
    workdir: str = DEFAULT_WORKDIR,
    timeout: int = DEFAULT_TIMEOUT,
    agy: str = AGY,
) -> Dict[str, Any]:
    if not (Path(agy).exists() or shutil.which(agy)):
        return _error_result(f"agy executable not found: {agy}")
    os.environ["TERM"] = "xterm"
    try:
        os.chdir(workdir)
    except OSError as exc:
        return _error_result(f"cannot chdir to {workdir}: {exc}")

    try:
        proc = _spawn_pty(agy)
    except Exception as exc:
        return _error_result(str(exc))

    chunks: list[str] = []
    stop_reader = threading.Event()

    def _reader() -> None:
        while not stop_reader.is_set():
            try:
                data = proc.read()
            except EOFError:
                break
            except Exception:
                break
            if data:
                chunks.append(data)
            elif not proc.isalive():
                break
            else:
                time.sleep(0.05)

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()

    deadline = time.time() + max(5, timeout)
    attempts = (True, True, False)

    try:
        time.sleep(1.2)
        for accept_with_tab in attempts:
            if time.time() >= deadline:
                break
            try:
                _drive_usage_command(proc, accept_with_tab=accept_with_tab)
            except Exception:
                break
            settle_until = min(deadline, time.time() + 4.0)
            while time.time() < settle_until:
                time.sleep(0.25)
                screen = "".join(chunks)
                if "Models & Quota" not in _strip_terminal(screen):
                    continue
                parsed = parse_usage_screen(screen)
                if parsed.get("confidence") == "real":
                    return parsed
        parsed = parse_usage_screen("".join(chunks))
        if parsed.get("confidence") == "real":
            return parsed
        return _error_result(parsed.get("error") or "capture failed", "".join(chunks), parsed.get("account"))
    finally:
        for exit_command in ("q", "\x1b", "/quit\r"):
            try:
                proc.write(exit_command)
                time.sleep(0.15)
            except Exception:
                break
        stop_reader.set()
        try:
            proc.terminate(force=True)
        except Exception:
            pass
        thread.join(timeout=1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture AGY /usage via ConPTY")
    parser.add_argument("--workdir", default=DEFAULT_WORKDIR)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--agy", default=AGY)
    args = parser.parse_args()
    payload = json.dumps(
        capture_usage(workdir=args.workdir, timeout=args.timeout, agy=args.agy),
        indent=2,
        ensure_ascii=False,
    )
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(payload)
    except Exception:
        sys.stdout.buffer.write(payload.encode("utf-8", errors="replace") + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
