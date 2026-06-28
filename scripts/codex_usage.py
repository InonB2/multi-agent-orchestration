#!/usr/bin/env python3
"""
codex_usage.py — read real usage from Codex local artifacts when available.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"


def empty_usage(source: str | None = None) -> dict:
    return {
        "tokens": None,
        "duration_ms": None,
        "window_pct": None,
        "source": source,
    }


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _usage_from_session_rows(rows: list[dict], source: str | None) -> dict:
    usage = empty_usage(source=source)
    for row in rows:
        if row.get("type") != "event_msg":
            continue
        payload = row.get("payload") or {}
        if payload.get("type") == "token_count":
            info = payload.get("info") or {}
            total = info.get("total_token_usage") or {}
            rate_limits = payload.get("rate_limits") or {}
            primary = rate_limits.get("primary") or {}
            usage["tokens"] = total.get("total_tokens")
            usage["window_pct"] = primary.get("used_percent")
        elif payload.get("type") == "task_complete":
            usage["duration_ms"] = payload.get("duration_ms")
    return usage


def read_usage_from_session_file(path: Path) -> dict:
    return _usage_from_session_rows(read_jsonl(path), str(path))


def read_latest_usage(sessions_root: Path = DEFAULT_SESSIONS_ROOT) -> dict:
    if not sessions_root.exists():
        return empty_usage()
    try:
        session_files = sorted(
            (path for path in sessions_root.glob("**/*.jsonl") if path.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return empty_usage()
    if not session_files:
        return empty_usage()
    return read_usage_from_session_file(session_files[0])


def _find_session_file_for_thread(sessions_root: Path, thread_id: str | None) -> Path | None:
    if not thread_id or not sessions_root.exists():
        return None
    matches = sorted(sessions_root.glob(f"**/*{thread_id}*.jsonl"))
    return matches[-1] if matches else None


def read_usage_from_exec_jsonl(exec_jsonl_path: Path, sessions_root: Path = DEFAULT_SESSIONS_ROOT) -> dict:
    rows = read_jsonl(exec_jsonl_path)
    thread_id = None
    exec_usage = None
    for row in rows:
        if row.get("type") == "thread.started":
            thread_id = row.get("thread_id")
        elif row.get("type") == "turn.completed" and isinstance(row.get("usage"), dict):
            exec_usage = row.get("usage")

    if exec_usage is None:
        return empty_usage(str(exec_jsonl_path))

    session_file = _find_session_file_for_thread(sessions_root, thread_id)
    if session_file is not None:
        session_usage = read_usage_from_session_file(session_file)
        if any(session_usage[key] is not None for key in ("tokens", "duration_ms", "window_pct")):
            return session_usage

    input_tokens = int(exec_usage.get("input_tokens") or 0)
    output_tokens = int(exec_usage.get("output_tokens") or 0)
    reasoning_tokens = int(exec_usage.get("reasoning_output_tokens") or 0)
    return {
        "tokens": max(input_tokens + output_tokens, input_tokens + output_tokens + reasoning_tokens),
        "duration_ms": None,
        "window_pct": None,
        "source": str(exec_jsonl_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read real Codex usage from local artifacts")
    parser.add_argument("--jsonl", type=Path, help="Optional codex exec --json output to parse")
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.jsonl:
        payload = read_usage_from_exec_jsonl(args.jsonl, sessions_root=args.sessions_root)
    else:
        payload = read_latest_usage(args.sessions_root)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
