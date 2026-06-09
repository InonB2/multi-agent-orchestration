#!/usr/bin/env python3
"""
checkpoint.py — Mid-task state saving and rate-limit resume queuing.

Commands:
    python scripts/checkpoint.py save --task TASK_ID \\
        --done "what's been completed" \\
        --remaining "what's left" \\
        --next "exact next step"

    python scripts/checkpoint.py read --task TASK_ID

    python scripts/checkpoint.py list-resumable

    python scripts/checkpoint.py mark-resumed --task TASK_ID

Files written:
    tasks/snapshots/[TASK_ID]_checkpoint.json  — full checkpoint per task
    tasks/queue/resume_queue.json              — array of pending resume entries
"""

import json
import re
import sys
import os
from datetime import datetime
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = ROOT / "tasks" / "snapshots"
QUEUE_DIR     = ROOT / "tasks" / "queue"
QUEUE_FILE    = QUEUE_DIR / "resume_queue.json"
TASKS_FILE    = ROOT / "tasks" / "active_tasks.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_dirs():
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)


_TASK_ID_RE = re.compile(r'^[A-Za-z0-9_\-]+$')


def _validate_task_id(task_id: str):
    """Reject task IDs that don't match the safe allowlist pattern."""
    if not task_id or not _TASK_ID_RE.match(task_id):
        print(
            "[ERROR] Invalid task_id '{}': must match ^[A-Za-z0-9_\\-]+$".format(task_id),
            file=sys.stderr,
        )
        sys.exit(1)


def _snapshot_path(task_id: str) -> Path:
    # BLOCKER-1: containment check — belt-and-suspenders after regex validation
    path = SNAPSHOTS_DIR / "{}_checkpoint.json".format(task_id)
    resolved = path.resolve()
    snapshots_resolved = SNAPSHOTS_DIR.resolve()
    if not str(resolved).startswith(str(snapshots_resolved) + os.sep) and \
            str(resolved) != str(snapshots_resolved):
        print("[ERROR] task_id resolves outside snapshots directory", file=sys.stderr)
        sys.exit(1)
    return path


def _read_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print("[WARN] Could not read resume_queue.json: {}".format(exc), file=sys.stderr)
        return []


def _write_queue(entries: list):
    _ensure_dirs()
    # MAJOR-1: atomic write — prevents corruption on interrupted write
    tmp = QUEUE_FILE.with_suffix('.tmp')
    tmp.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, QUEUE_FILE)


def _lookup_task(task_id: str) -> dict:
    """Return the task dict from active_tasks.json or empty dict."""
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        for t in data.get("tasks", []):
            if t.get("task_id") == task_id:
                return t
    except Exception:
        pass
    return {}


def _current_model(task: dict) -> str:
    """Best-effort: read preferred_provider from task, fallback to 'unknown'."""
    return task.get("preferred_provider", task.get("assigned_to", "unknown"))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_save(args):
    """Save a checkpoint for a task."""
    # Parse flags
    task_id       = _get_flag(args, "--task")
    done          = _get_flag(args, "--done", required=False) or ""
    remaining     = _get_flag(args, "--remaining", required=False) or ""
    next_step     = _get_flag(args, "--next", required=False) or ""
    interrupted_by = _get_flag(args, "--interrupted-by", required=False) or "manual"
    model_override = _get_flag(args, "--model", required=False)

    if not task_id:
        print("[ERROR] --task TASK_ID is required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # BLOCKER-1 / MAJOR-3

    _ensure_dirs()

    task = _lookup_task(task_id)
    model = model_override or _current_model(task)

    # Build acceptance criteria from task spec if available
    acceptance_criteria = ""
    if task:
        notes = task.get("notes", "") or ""
        # Try to extract success criteria hints from notes
        for line in notes.split("."):
            if any(kw in line.lower() for kw in ["success", "criteria", "done when", "pass"]):
                acceptance_criteria = line.strip()
                break
        if not acceptance_criteria:
            acceptance_criteria = task.get("title", "")

    checkpoint = {
        "task_id": task_id,
        "model": model,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "done": done,
        "remaining": remaining,
        "next_step": next_step,
        "acceptance_criteria": acceptance_criteria,
        "interrupted_by": interrupted_by,
    }

    snap_path = _snapshot_path(task_id)
    # MAJOR-1: atomic write — prevents corruption on interrupted write
    tmp_snap = snap_path.with_suffix('.tmp')
    tmp_snap.write_text(
        json.dumps(checkpoint, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp_snap, snap_path)
    print("[OK] Checkpoint saved: {}".format(snap_path))

    # Append to resume queue (avoid duplicates — replace existing entry for same task_id)
    queue = _read_queue()
    queue = [e for e in queue if e.get("task_id") != task_id]
    queue.append({
        "task_id": task_id,
        "model": model,
        "resume_at_estimate": "next_session",
        "interrupted_by": interrupted_by,
        "checkpoint_path": str(snap_path.relative_to(ROOT)),  # BLOCKER-3: store relative, not absolute
        "queued_at": datetime.utcnow().isoformat() + "Z",
        "resumed": False,
    })
    _write_queue(queue)
    print("[OK] Added to resume queue: {}".format(QUEUE_FILE))


def cmd_read(args):
    """Print checkpoint for a task."""
    task_id = _get_flag(args, "--task")
    if not task_id:
        print("[ERROR] --task TASK_ID is required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # MAJOR-3

    snap_path = _snapshot_path(task_id)
    if not snap_path.exists():
        print("[WARN] No checkpoint found for task '{}'".format(task_id))
        sys.exit(0)

    data = json.loads(snap_path.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_list_resumable(args):
    """List all tasks in resume_queue.json that haven't been resumed yet."""
    queue = _read_queue()
    pending = [e for e in queue if not e.get("resumed", False)]

    if not pending:
        print("[OK] No pending resumable tasks.")
        return

    print("Resumable tasks ({}):\n".format(len(pending)))
    for entry in pending:
        print("  [{}]  model={}  interrupted_by={}  queued_at={}".format(
            entry.get("task_id", "?"),
            entry.get("model", "?"),
            entry.get("interrupted_by", "?"),
            entry.get("queued_at", "?"),
        ))
        chk_rel = entry.get("checkpoint_path", "")
        chk_display = str(ROOT / chk_rel) if chk_rel else "?"
        print("         checkpoint: {}".format(chk_display))  # BLOCKER-3: reconstruct abs path from stored relative
        print()


def cmd_mark_resumed(args):
    """Remove a task from the resume queue (called when resumed)."""
    task_id = _get_flag(args, "--task")
    if not task_id:
        print("[ERROR] --task TASK_ID is required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # MAJOR-3

    queue = _read_queue()
    before_count = len(queue)
    queue = [e for e in queue if e.get("task_id") != task_id]

    if len(queue) == before_count:
        print("[WARN] Task '{}' not found in resume queue.".format(task_id))
    else:
        _write_queue(queue)
        print("[OK] Task '{}' removed from resume queue.".format(task_id))


# ---------------------------------------------------------------------------
# Flag parser
# ---------------------------------------------------------------------------

def _get_flag(args: list, flag: str, required: bool = True) -> str:
    """Return the value following `flag` in args list."""
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            return args[idx + 1]
        elif idx + 1 < len(args):
            return args[idx + 1]
    if required:
        print("[ERROR] {} is required".format(flag), file=sys.stderr)
        sys.exit(1)
    return ""


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "save": cmd_save,
    "read": cmd_read,
    "list-resumable": cmd_list_resumable,
    "mark-resumed": cmd_mark_resumed,
}


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    if cmd not in COMMANDS:
        print("[ERROR] Unknown command '{}'. Valid: {}".format(
            cmd, ", ".join(COMMANDS.keys())), file=sys.stderr)
        print("\nRun with --help for usage.")
        sys.exit(1)

    COMMANDS[cmd](args[1:])


if __name__ == "__main__":
    main()
