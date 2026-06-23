#!/usr/bin/env python3
"""
rate_wall_watchdog.py — guard against dispatching to a rate-walled engine.

WHAT THE RATE WALL IS
---------------------
Codex enforces two rolling usage windows: a PRIMARY 5-hour window and a
SECONDARY weekly window. Each is reported in the codex session JSONL under
payload.rate_limits.{primary,secondary} as {used_percent, resets_at (epoch
seconds), window_minutes}. When a window hits 100% the CLI starts returning
rate-limit errors.

WHAT HAPPENS WHEN THE WALL HITS (answers for the playbook)
----------------------------------------------------------
1. The codex CLI returns rate-limit errors and refuses new turns.
2. Any in-flight sub-agent job dies mid-turn — it is NOT paused, it is killed.
3. Our dashboard activity entry for that worker can become a stale "ghost":
   it still shows status=running because the worker never reached its
   complete() call to clear itself. (Mitigation: a sweeper should clear
   workers whose engine is walled; this watchdog surfaces the wall so that
   sweep can run.)

HOW TO KNOW WHEN IT RETURNS
---------------------------
Each window carries resets_at (epoch seconds). Convert to local time; that is
when the binding window drops back under 100% and dispatch is safe again.

WAKE-UP STRATEGY
----------------
We CANNOT revive a dead sub-agent mid-flight. After the reset we re-dispatch
FRESH and rely on MMOI v2 checkpoint/resume to continue long jobs from their
last checkpoint instead of restarting from zero. A scheduled re-dispatch
should target the reset time of the binding window.

COMMANDS
--------
    python scripts/rate_wall_watchdog.py check
        Print current %, which window is binding, and the local reset time for
        any window >= 90%.

    python scripts/rate_wall_watchdog.py should-dispatch --engine codex
        Exit 0 if safe to dispatch; non-zero (and print reset time) if codex is
        walled (>= 99% on either window). Lets Andy skip a walled engine.

stdlib-only. Non-codex engines are always dispatchable here (agy/claude expose
no comparable wall telemetry locally).
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import codex_usage

WALL_WARN_PCT = 90.0   # surface in `check`
WALL_BLOCK_PCT = 99.0  # block dispatch in `should-dispatch`


def _epoch_to_local(epoch: float | int | None) -> str | None:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch)).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except (ValueError, OSError, OverflowError):
        return None


def read_codex_windows(sessions_root: Path = codex_usage.DEFAULT_SESSIONS_ROOT) -> dict:
    """Return both rate-limit windows from the most recent codex session.

    Shape:
        {
          "found": bool,
          "source": str | None,
          "windows": {
             "primary":   {"used_percent": float|None, "resets_at": int|None,
                           "resets_local": str|None, "window_minutes": int|None},
             "secondary": {...same...},
          }
        }
    Missing windows are returned with None fields rather than omitted.
    """
    empty_window = {"used_percent": None, "resets_at": None, "resets_local": None, "window_minutes": None}
    result = {
        "found": False,
        "source": None,
        "windows": {"primary": dict(empty_window), "secondary": dict(empty_window)},
    }
    if not sessions_root.exists():
        return result
    try:
        session_files = sorted(
            (p for p in sessions_root.glob("**/*.jsonl") if p.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return result
    if not session_files:
        return result

    path = session_files[0]
    result["source"] = str(path)
    rate_limits = None
    for row in codex_usage.read_jsonl(path):
        if row.get("type") != "event_msg":
            continue
        payload = row.get("payload") or {}
        if payload.get("type") == "token_count":
            rl = payload.get("rate_limits")
            if isinstance(rl, dict):
                rate_limits = rl  # keep latest

    if not rate_limits:
        return result

    result["found"] = True
    for key in ("primary", "secondary"):
        win = rate_limits.get(key)
        if not isinstance(win, dict):
            continue
        used = win.get("used_percent")
        resets_at = win.get("resets_at")
        result["windows"][key] = {
            "used_percent": (float(used) if used is not None else None),
            "resets_at": resets_at,
            "resets_local": _epoch_to_local(resets_at),
            "window_minutes": win.get("window_minutes"),
        }
    return result


def _binding_window(windows: dict) -> tuple[str | None, dict]:
    """Return (name, window) with the highest used_percent."""
    best_name = None
    best = {"used_percent": None}
    for name, win in windows.items():
        pct = win.get("used_percent")
        if pct is None:
            continue
        if best["used_percent"] is None or pct > best["used_percent"]:
            best_name, best = name, win
    return best_name, best


def cmd_check(_: argparse.Namespace) -> int:
    state = read_codex_windows()
    if not state["found"]:
        print("codex rate windows: no telemetry found (no recent session with rate_limits).")
        print("  source: {}".format(state["source"] or "<none>"))
        return 0

    print("codex rate windows (source: {}):".format(state["source"]))
    for name in ("primary", "secondary"):
        win = state["windows"][name]
        pct = win.get("used_percent")
        if pct is None:
            print("  {:<10} : (not reported)".format(name))
            continue
        label = "5h" if name == "primary" else "weekly"
        line = "  {:<10} ({}) : {:.1f}% used".format(name, label, pct)
        if pct >= WALL_WARN_PCT:
            line += "  [>= {:.0f}% — reset at {}]".format(
                WALL_WARN_PCT, win.get("resets_local") or "unknown")
        print(line)

    name, win = _binding_window(state["windows"])
    if name is None:
        print("binding window: none reported")
    else:
        print("binding window: {} at {:.1f}%".format(name, win["used_percent"]))
        if win["used_percent"] >= WALL_WARN_PCT:
            print("  reset (local): {}".format(win.get("resets_local") or "unknown"))
    return 0


def cmd_should_dispatch(args: argparse.Namespace) -> int:
    engine = args.engine.lower()
    if engine != "codex":
        # agy/claude have no comparable local wall telemetry — never block here.
        print("{}: no rate-wall telemetry; safe to dispatch.".format(engine))
        return 0

    state = read_codex_windows()
    if not state["found"]:
        # Fail-open: no telemetry means we cannot prove a wall; allow dispatch.
        print("codex: no rate telemetry found; assuming safe to dispatch.")
        return 0

    walled = []
    for name, win in state["windows"].items():
        pct = win.get("used_percent")
        if pct is not None and pct >= WALL_BLOCK_PCT:
            walled.append((name, win))

    if not walled:
        name, win = _binding_window(state["windows"])
        pct = win.get("used_percent")
        if pct is None:
            print("codex: safe to dispatch (no window reported).")
        else:
            print("codex: safe to dispatch (binding {} at {:.1f}%, below {:.0f}%).".format(
                name, pct, WALL_BLOCK_PCT))
        return 0

    # Walled on at least one window — block, print reset times.
    print("codex: WALLED — do NOT dispatch.")
    for name, win in walled:
        print("  {} at {:.1f}% — resets (local): {}".format(
            name, win["used_percent"], win.get("resets_local") or "unknown"))
    # Recommend the earliest reset as the re-dispatch target.
    resets = [w.get("resets_at") for _, w in walled if w.get("resets_at") is not None]
    if resets:
        earliest = min(resets)
        print("  re-dispatch target (earliest reset, local): {}".format(_epoch_to_local(earliest)))
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex rate-wall watchdog")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Print current % and reset times")
    p_check.set_defaults(func=cmd_check)

    p_disp = sub.add_parser("should-dispatch", help="Exit non-zero if engine is walled")
    p_disp.add_argument("--engine", required=True)
    p_disp.set_defaults(func=cmd_should_dispatch)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
