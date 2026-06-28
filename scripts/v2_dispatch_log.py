#!/usr/bin/env python3
"""
v2_dispatch_log.py — Append learning-loop dispatch decisions to tasks/v2_dispatch_log.jsonl.

Command:
    python scripts/v2_dispatch_log.py log --task-id MMOI-201 --summary "Route task" --recommended-model claude-opus-4.8 --recommended-effort high --recommended-by root --decided-model gpt-5 --decided-effort medium --decided-by local-orchestrator --reason "Codex is better for repo surgery"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "tasks" / "v2_dispatch_log.jsonl"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_record(record: dict, path: Path = LOG_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def cmd_log(args: argparse.Namespace) -> int:
    record = {
        "timestamp": now_iso(),
        "task_id": args.task_id,
        "summary": args.summary,
        "recommended_model": args.recommended_model,
        "recommended_effort": args.recommended_effort,
        "recommended_by": args.recommended_by,
        "decided_model": args.decided_model,
        "decided_effort": args.decided_effort,
        "decided_by": args.decided_by,
        "reason": args.reason,
    }
    append_record(record, LOG_FILE)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append a v2 dispatch learning-log record")
    subparsers = parser.add_subparsers(dest="command", required=True)

    log_parser = subparsers.add_parser("log", help="Append one decision record")
    log_parser.add_argument("--task-id", required=True)
    log_parser.add_argument("--summary", required=True)
    log_parser.add_argument("--recommended-model", required=True)
    log_parser.add_argument("--recommended-effort", required=True)
    log_parser.add_argument("--recommended-by", required=True)
    log_parser.add_argument("--decided-model", required=True)
    log_parser.add_argument("--decided-effort", required=True)
    log_parser.add_argument("--decided-by", required=True)
    log_parser.add_argument("--reason", required=True)
    log_parser.set_defaults(func=cmd_log)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
