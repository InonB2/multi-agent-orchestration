#!/usr/bin/env python3
"""
Normalize usage from a `codex exec --json` run into one dashboard-friendly record.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
DEFAULT_OUTPUT_PATH = ROOT / "logs" / "usage.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def find_session_file(sessions_root: Path, thread_id: str) -> Path | None:
    matches = sorted(sessions_root.glob(f"**/*{thread_id}*.jsonl"))
    return matches[-1] if matches else None


def extract_record(
    exec_jsonl_path: Path,
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
    task_id: str | None = None,
    worker: str | None = None,
) -> dict:
    exec_rows = read_jsonl(exec_jsonl_path)
    thread_id = None
    usage = None

    for row in exec_rows:
        if row.get("type") == "thread.started":
            thread_id = row.get("thread_id")
        elif row.get("type") == "turn.completed" and isinstance(row.get("usage"), dict):
            usage = row["usage"]

    if not thread_id:
        raise ValueError(f"No thread id found in {exec_jsonl_path}")
    if not usage:
        raise ValueError(f"No usage block found in {exec_jsonl_path}")

    record = {
        "ts": None,
        "task_id": task_id,
        "worker": worker,
        "engine": "codex",
        "thread_id": thread_id,
        "tokens": None,
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_output_tokens": usage.get("reasoning_output_tokens"),
        "cost": None,
        "duration_ms": None,
        "ttft_ms": None,
        "window_pct": None,
        "weekly_window_pct": None,
        "plan_type": None,
        "source": str(exec_jsonl_path),
        "session_file": None,
    }

    session_file = find_session_file(sessions_root, thread_id)
    if session_file is None:
        input_tokens = int(record["input_tokens"] or 0)
        output_tokens = int(record["output_tokens"] or 0)
        reasoning_output_tokens = int(record["reasoning_output_tokens"] or 0)
        record["tokens"] = input_tokens + output_tokens
        if reasoning_output_tokens:
            record["tokens"] = max(record["tokens"], input_tokens + reasoning_output_tokens + output_tokens)
        return record

    session_rows = read_jsonl(session_file)
    record["session_file"] = str(session_file)

    for row in session_rows:
        if record["ts"] is None and row.get("timestamp"):
            record["ts"] = row.get("timestamp")
        if row.get("type") != "event_msg":
            continue
        payload = row.get("payload") or {}
        if payload.get("type") == "token_count":
            info = payload.get("info") or {}
            total = info.get("total_token_usage") or {}
            rate_limits = payload.get("rate_limits") or {}
            primary = rate_limits.get("primary") or {}
            secondary = rate_limits.get("secondary") or {}
            record["tokens"] = total.get("total_tokens")
            record["window_pct"] = primary.get("used_percent")
            record["weekly_window_pct"] = secondary.get("used_percent")
            record["plan_type"] = rate_limits.get("plan_type")
        elif payload.get("type") == "task_complete":
            record["duration_ms"] = payload.get("duration_ms")
            record["ttft_ms"] = payload.get("time_to_first_token_ms")

    if record["tokens"] is None:
        input_tokens = int(record["input_tokens"] or 0)
        output_tokens = int(record["output_tokens"] or 0)
        reasoning_output_tokens = int(record["reasoning_output_tokens"] or 0)
        record["tokens"] = max(input_tokens + output_tokens, input_tokens + reasoning_output_tokens + output_tokens)

    return record


def append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract usage from codex exec JSONL output")
    parser.add_argument("--jsonl", required=True, type=Path, help="Path to codex exec --json output")
    parser.add_argument("--task-id")
    parser.add_argument("--worker")
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    parser.add_argument("--append", type=Path, help="Optional JSONL output file to append record to")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    record = extract_record(
        exec_jsonl_path=args.jsonl,
        sessions_root=args.sessions_root,
        task_id=args.task_id,
        worker=args.worker,
    )
    if args.append:
        append_jsonl(args.append, record)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
