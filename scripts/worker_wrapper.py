#!/usr/bin/env python3
"""
worker_wrapper.py — Deterministic result writeback for ToT workers.

Every worker in the Team-of-Teams pool writes its final deliverable to a unique,
deterministic path so concurrent workers never collide in owner_inbox/:

    owner_inbox/TASK-<ID>_result.md

This module is the single enforcement point for that naming rule. The supervisor
(model_supervisor.py) calls write_result() after a worker finishes; a worker CLI
can also call it directly.

Writes are atomic (temp file + os.replace) to avoid half-written results, and the
task ID is validated against the same allowlist coordinator.py uses, blocking path
traversal into directories outside owner_inbox/.

Commands:
    python scripts/worker_wrapper.py path  --task-id ID
    python scripts/worker_wrapper.py write --task-id ID --content "..."
    python scripts/worker_wrapper.py write --task-id ID --content-file FILE
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
OWNER_INBOX   = ROOT / "owner_inbox"
_TASK_ID_RE   = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_task_id(task_id: str) -> str:
    if not task_id or not _TASK_ID_RE.match(task_id):
        raise ValueError(
            "Invalid task_id '{}': must match ^[A-Za-z0-9_\\-]+$".format(task_id)
        )
    return task_id


def result_path(task_id: str) -> Path:
    """Return the deterministic deliverable path for *task_id*.

    The resolved path is asserted to stay inside owner_inbox/ as a belt-and-
    suspenders guard against traversal even if the regex is ever loosened.
    """
    normalized = _validate_task_id(task_id)
    if normalized.startswith("TASK-"):
        normalized = normalized[len("TASK-"):]
    path = (OWNER_INBOX / "TASK-{}_result.md".format(normalized)).resolve()
    try:
        path.relative_to(OWNER_INBOX.resolve())
    except ValueError:
        raise ValueError("Refusing to write outside owner_inbox/: {}".format(path))
    return path


def write_result(task_id: str, content: str) -> Path:
    """Atomically write *content* to the deterministic result path. Returns the path."""
    path = result_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_path(args) -> None:
    try:
        print(result_path(args.task_id))
    except ValueError as exc:
        print("[ERROR] {}".format(exc), file=sys.stderr)
        sys.exit(1)


def cmd_write(args) -> None:
    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")
    elif args.content is not None:
        content = args.content
    else:
        # Allow piping content on stdin as a fallback.
        content = sys.stdin.read()
    try:
        path = write_result(args.task_id, content)
    except ValueError as exc:
        print("[ERROR] {}".format(exc), file=sys.stderr)
        sys.exit(1)
    print("[OK] result written: {}".format(path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic result writeback for ToT workers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_path = sub.add_parser("path", help="Print the deterministic result path for a task")
    p_path.add_argument("--task-id", required=True)

    p_write = sub.add_parser("write", help="Write a result to the deterministic path")
    p_write.add_argument("--task-id", required=True)
    p_write.add_argument("--content", help="Inline result content")
    p_write.add_argument("--content-file", help="Read result content from this file")

    args = parser.parse_args()
    {"path": cmd_path, "write": cmd_write}[args.command](args)


if __name__ == "__main__":
    main()
