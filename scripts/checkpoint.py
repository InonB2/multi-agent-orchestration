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

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = ROOT / "tasks" / "snapshots"
QUEUE_DIR     = ROOT / "tasks" / "queue"
QUEUE_FILE    = QUEUE_DIR / "resume_queue.json"
TASKS_FILE    = ROOT / "tasks" / "active_tasks.json"


# ---------------------------------------------------------------------------
# File-lock helpers (cross-platform sidecar-file pattern)
# Used to guard concurrent writes to the resume queue.
# ---------------------------------------------------------------------------

def _acquire_lock(lock_path: Path, timeout: int = 10) -> bool:
    """Try to create *lock_path* exclusively. Returns True on success, False on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            time.sleep(0.05)
    return False


def _release_lock(lock_path: Path) -> None:
    """Delete the sidecar lock file, ignoring missing-file errors."""
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


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
    """Atomically write the resume queue, protected by a sidecar lock file."""
    _ensure_dirs()
    lock_path = Path(str(QUEUE_FILE) + ".lock")
    if not _acquire_lock(lock_path):
        print("[ERROR] Could not acquire lock on queue file", file=sys.stderr)
        sys.exit(1)
    try:
        tmp = QUEUE_FILE.with_suffix('.tmp')
        tmp.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, QUEUE_FILE)
    finally:
        _release_lock(lock_path)


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


def read_checkpoint(task_id: str):
    """Return checkpoint JSON for *task_id*, or None when absent/corrupt."""
    snap_path = _snapshot_path(task_id)
    if not snap_path.exists():
        return None
    try:
        return json.loads(snap_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def resume_queue_entry(task_id: str):
    """Return the pending resume-queue entry for *task_id*, or None."""
    for entry in _read_queue():
        if entry.get("task_id") == task_id and not entry.get("resumed", False):
            return entry
    return None


def load_resume_context(task_id: str):
    """Return saved checkpoint context for a queued resumable task, or None."""
    entry = resume_queue_entry(task_id)
    if not entry:
        return None

    chk_rel = entry.get("checkpoint_path", "")
    chk_path = (ROOT / chk_rel) if chk_rel else _snapshot_path(task_id)
    try:
        return json.loads(chk_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def mark_resumed(task_id: str) -> bool:
    """Remove *task_id* from the resume queue. Returns True when removed."""
    queue = _read_queue()
    before_count = len(queue)
    queue = [e for e in queue if e.get("task_id") != task_id]
    if len(queue) == before_count:
        return False
    _write_queue(queue)
    return True


# Sentence-level signal words that indicate acceptance criteria.
# Only sentences STARTING with these words are treated as criteria to avoid
# false positives from words like "bypass" (pass), "password" (pass), "surpass" (pass),
# or version numbers broken by the period-split heuristic.
_CRITERIA_SIGNAL_WORDS = (
    "all ", "must ", "should ", "verify ", "test ", "check ", "pass ",
)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_save(args):
    """Save a checkpoint for a task."""
    # Parse flags
    task_id        = _get_flag(args, "--task")
    done           = _get_flag(args, "--done", required=False) or ""
    remaining      = _get_flag(args, "--remaining", required=False) or ""
    next_step      = _get_flag(args, "--next", required=False) or ""
    interrupted_by = _get_flag(args, "--interrupted-by", required=False) or "manual"
    model_override = _get_flag(args, "--model", required=False)

    if not task_id:
        print("[ERROR] --task TASK_ID is required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # BLOCKER-1 / MAJOR-3

    _ensure_dirs()

    task = _lookup_task(task_id)
    model = model_override or _current_model(task)

    # Build acceptance criteria from task spec if available.
    # Conservative heuristic: only select a sentence as criteria if it STARTS with
    # a known signal word.  This avoids matching "bypass", "password", "surpass", etc.
    acceptance_criteria = ""
    if task:
        notes = task.get("notes", "") or ""
        for sentence in notes.split("."):
            stripped = sentence.strip()
            lower_stripped = stripped.lower()
            if any(lower_stripped.startswith(signal) for signal in _CRITERIA_SIGNAL_WORDS):
                acceptance_criteria = stripped
                break
        # If no confident match found, leave acceptance_criteria as empty string
        # rather than falling back to a potentially noisy title.

    checkpoint = {
        "task_id": task_id,
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
        "queued_at": datetime.now(timezone.utc).isoformat(),
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

    # EDGE-3: guard against corrupt checkpoint files instead of crashing
    data = read_checkpoint(task_id)
    if data is None:
        print(
            "[ERROR] Checkpoint file is corrupt for task '{}'.".format(task_id),
            file=sys.stderr,
        )
        sys.exit(1)
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

    if not mark_resumed(task_id):
        print("[WARN] Task '{}' not found in resume queue.".format(task_id))
    else:
        print("[OK] Task '{}' removed from resume queue.".format(task_id))


# ---------------------------------------------------------------------------
# Flag parser  (BUG-1 fix: removed the elif branch that allowed --flag as value)
# ---------------------------------------------------------------------------

def _get_flag(args: list, flag: str, required: bool = True) -> str:
    """Return the value following *flag* in args list.

    Only returns the next token if it does NOT start with '--', preventing a
    flag name from being silently accepted as a value (e.g. --task --done).
    If no valid value is found and required=True, exits with a clear error.
    """
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args) and not args[idx + 1].startswith("--"):
            return args[idx + 1]
    if required:
        print("[ERROR] Flag {} requires a value".format(flag), file=sys.stderr)
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
