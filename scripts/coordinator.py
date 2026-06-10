#!/usr/bin/env python3
"""
coordinator.py — Task lifecycle manager for multi-model AI agents.

Manages claim, track, checkpoint, and complete for tasks in active_tasks.json.
Integrates with task_tracker.py and checkpoint.py rather than duplicating logic.

Kanban flow: Backlog -> In Progress -> Blocked -> Tested -> Done
  mark-tested sets status="tested" (QA-signed-off, NOT yet final).
  mark-done   sets status="done"   (final terminal state, after QA approval).

Commands:
    python scripts/coordinator.py claim --task TASK_ID --model MODEL_NAME

    python scripts/coordinator.py update --task TASK_ID --phase PHASE \\
        [--note "optional note"]

    python scripts/coordinator.py checkpoint --task TASK_ID \\
        --done "what's been completed" \\
        --remaining "what's left" \\
        --next "exact next step"

    python scripts/coordinator.py mark-tested --task TASK_ID \\
        --result-path path/to/output.md

    python scripts/coordinator.py mark-done --task TASK_ID

    python scripts/coordinator.py status --task TASK_ID

    python scripts/coordinator.py list-mine --model MODEL_NAME

Models: claude-code, codex, antigravity
"""

# QA-4: Python 3.8 compatibility — enables 'dict | None' annotation
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
TASKS_FILE    = ROOT / "tasks" / "active_tasks.json"
CHECKPOINT_PY = Path(__file__).resolve().parent / "checkpoint.py"


# ---------------------------------------------------------------------------
# File-lock helpers (cross-platform sidecar-file pattern)  [REL-2]
# All scripts that read-modify-write active_tasks.json use the same lock path.
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
# Active tasks I/O
# ---------------------------------------------------------------------------

def _load_tasks() -> dict:
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("[ERROR] tasks/active_tasks.json not found: {}".format(TASKS_FILE), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print("[ERROR] JSON parse error: {}".format(exc), file=sys.stderr)
        sys.exit(1)

    # QA-5: Normalise legacy "in-progress" to canonical "in_progress" for backward compat.
    # The canonical status written by all current commands is "in_progress".
    for task in data.get("tasks", []):
        if task.get("status") == "in-progress":
            task["status"] = "in_progress"

    return data


def _save_tasks(data: dict):
    # MAJOR-1: atomic write — prevents corruption on interrupted write
    tmp = TASKS_FILE.with_suffix('.tmp')
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, TASKS_FILE)


def _find_task(data: dict, task_id: str) -> dict | None:
    """Return mutable reference to the task dict, or None."""
    for t in data.get("tasks", []):
        if t.get("task_id") == task_id:
            return t
    return None


def _timestamp() -> str:
    return datetime.utcnow().isoformat() + "Z"


_TASK_ID_RE = re.compile(r'^[A-Za-z0-9_\-]+$')


def _validate_task_id(task_id: str):
    """Reject task IDs that don't match the safe allowlist pattern. MAJOR-3 / BLOCKER-1."""
    if not task_id or not _TASK_ID_RE.match(task_id):
        print(
            "[ERROR] Invalid task_id '{}': must match ^[A-Za-z0-9_\\-]+$".format(task_id),
            file=sys.stderr,
        )
        sys.exit(1)


def _append_log_entry(task: dict, entry: str):
    """Append a timestamped log line to task['coordinator_log']."""
    log = task.get("coordinator_log", [])
    log.append("{} {}".format(_timestamp(), entry))
    task["coordinator_log"] = log


# ---------------------------------------------------------------------------
# External script caller  [REL-3]
# ---------------------------------------------------------------------------

def _run_checkpoint(extra_args: list, task_id: str = "", phase: str = "") -> int:
    """Call checkpoint.py with the given args.

    Uses capture_output=True so failures produce coordinator-context messages
    rather than raw subprocess noise with no surrounding context.
    Returns the subprocess returncode.
    """
    result = subprocess.run(
        [sys.executable, str(CHECKPOINT_PY)] + extra_args,
        capture_output=True,
    )
    if result.returncode != 0:
        label = " (task: {}, phase: {})".format(task_id, phase) if task_id else ""
        print(
            "[ERROR] checkpoint.py failed{}.".format(label),
            file=sys.stderr,
        )
        if result.stderr:
            print(result.stderr.decode(errors="replace"), file=sys.stderr)
    return result.returncode


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_claim(args):
    """
    Claim a task: set status=in_progress, preferred_provider=MODEL, phase=claimed.
    """
    task_id = _get_flag(args, "--task")
    model   = _get_flag(args, "--model")

    if not task_id or not model:
        print("[ERROR] --task and --model are required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # MAJOR-3

    # REL-2: acquire lock before read-modify-write cycle
    lock_path = Path(str(TASKS_FILE) + ".lock")
    if not _acquire_lock(lock_path):
        print("[ERROR] Could not acquire lock on tasks file", file=sys.stderr)
        sys.exit(1)
    try:
        data = _load_tasks()
        task = _find_task(data, task_id)
        if task is None:
            print("[ERROR] Task '{}' not found in active_tasks.json".format(task_id), file=sys.stderr)
            sys.exit(1)

        task["status"] = "in_progress"
        task["preferred_provider"] = model
        task["phase"] = "claimed"
        task["claimed_at"] = _timestamp()
        _append_log_entry(task, "CLAIMED by {}".format(model))

        _save_tasks(data)
    finally:
        _release_lock(lock_path)

    print("[OK] Task '{}' claimed by {} (status=in_progress, phase=claimed)".format(task_id, model))


def cmd_update(args):
    """
    Update phase field + append to task log.
    """
    task_id = _get_flag(args, "--task")
    phase   = _get_flag(args, "--phase")
    note    = _get_flag(args, "--note", required=False) or ""

    if not task_id or not phase:
        print("[ERROR] --task and --phase are required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # MAJOR-3

    # REL-2: acquire lock before read-modify-write cycle
    lock_path = Path(str(TASKS_FILE) + ".lock")
    if not _acquire_lock(lock_path):
        print("[ERROR] Could not acquire lock on tasks file", file=sys.stderr)
        sys.exit(1)
    try:
        data = _load_tasks()
        task = _find_task(data, task_id)
        if task is None:
            print("[ERROR] Task '{}' not found".format(task_id), file=sys.stderr)
            sys.exit(1)

        task["phase"] = phase
        log_line = "PHASE -> {}".format(phase)
        if note:
            log_line += " | {}".format(note)
        _append_log_entry(task, log_line)

        _save_tasks(data)
    finally:
        _release_lock(lock_path)

    print("[OK] Task '{}' phase updated to '{}'".format(task_id, phase))
    if note:
        print("     Note: {}".format(note))


def cmd_checkpoint(args):
    """
    Convenience wrapper around checkpoint.py save.
    Passes all relevant flags through.
    """
    task_id   = _get_flag(args, "--task")
    done      = _get_flag(args, "--done", required=False) or ""
    remaining = _get_flag(args, "--remaining", required=False) or ""
    next_step = _get_flag(args, "--next", required=False) or ""
    model     = _get_flag(args, "--model", required=False) or ""
    interrupted_by = _get_flag(args, "--interrupted-by", required=False) or "manual"

    if not task_id:
        print("[ERROR] --task TASK_ID is required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # MAJOR-3

    checkpoint_args = ["save", "--task", task_id,
                       "--done", done,
                       "--remaining", remaining,
                       "--next", next_step,
                       "--interrupted-by", interrupted_by]
    if model:
        checkpoint_args += ["--model", model]

    rc = _run_checkpoint(checkpoint_args, task_id=task_id, phase="checkpoint")
    if rc == 0:
        # REL-2: acquire lock before read-modify-write cycle
        lock_path = Path(str(TASKS_FILE) + ".lock")
        if not _acquire_lock(lock_path):
            print("[ERROR] Could not acquire lock on tasks file", file=sys.stderr)
            sys.exit(1)
        try:
            data = _load_tasks()
            task = _find_task(data, task_id)
            if task:
                task["phase"] = "checkpointed"
                _append_log_entry(task, "CHECKPOINTED interrupted_by={}".format(interrupted_by))
                _save_tasks(data)
        finally:
            _release_lock(lock_path)
    else:
        sys.exit(rc)


def cmd_mark_tested(args):
    """
    Mark task as tested (QA-signed-off), phase=done.

    Kanban note: "tested" means QA has signed off — it is NOT the final state.
    Use mark-done after QA approval to set the terminal status="done".
    """
    task_id     = _get_flag(args, "--task")
    result_path = _get_flag(args, "--result-path", required=False) or ""

    if not task_id:
        print("[ERROR] --task is required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # MAJOR-3

    # REL-2: acquire lock before read-modify-write cycle
    lock_path = Path(str(TASKS_FILE) + ".lock")
    if not _acquire_lock(lock_path):
        print("[ERROR] Could not acquire lock on tasks file", file=sys.stderr)
        sys.exit(1)
    try:
        data = _load_tasks()
        task = _find_task(data, task_id)
        if task is None:
            print("[ERROR] Task '{}' not found".format(task_id), file=sys.stderr)
            sys.exit(1)

        task["status"] = "tested"
        task["phase"] = "done"
        task["completed_at"] = _timestamp()

        if result_path:
            notes = task.get("notes", "") or ""
            result_note = "Result: {}".format(result_path)
            task["notes"] = (notes + "\n" + result_note).strip() if notes else result_note

        _append_log_entry(task, "MARK-TESTED result={}".format(result_path or "(no path)"))

        _save_tasks(data)
    finally:
        _release_lock(lock_path)

    print("[OK] Task '{}' marked tested (status=tested, phase=done)".format(task_id))
    if result_path:
        print("     Result path: {}".format(result_path))

    # Remove from resume queue if it was checkpointed.  EDGE-6: log failure instead of silencing.
    try:
        _run_checkpoint(["mark-resumed", "--task", task_id], task_id=task_id, phase="mark-tested")
    except Exception as exc:
        print(
            "[WARN] Could not clear checkpoint for task '{}': {}".format(task_id, exc),
            file=sys.stderr,
        )


def cmd_mark_done(args):
    """
    Mark task as done — the final terminal state after QA approval.

    Kanban flow: Tested -> Done.
    Only call this AFTER a QA agent has signed off (i.e., after mark-tested).
    """
    task_id = _get_flag(args, "--task")

    if not task_id:
        print("[ERROR] --task is required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)

    # REL-2: acquire lock before read-modify-write cycle
    lock_path = Path(str(TASKS_FILE) + ".lock")
    if not _acquire_lock(lock_path):
        print("[ERROR] Could not acquire lock on tasks file", file=sys.stderr)
        sys.exit(1)
    try:
        data = _load_tasks()
        task = _find_task(data, task_id)
        if task is None:
            print("[ERROR] Task '{}' not found".format(task_id), file=sys.stderr)
            sys.exit(1)

        task["status"] = "done"
        task["closed_at"] = _timestamp()
        _append_log_entry(task, "MARK-DONE (final terminal state)")

        _save_tasks(data)
    finally:
        _release_lock(lock_path)

    print("[OK] Task '{}' marked done (status=done — final terminal state)".format(task_id))


def cmd_status(args):
    """
    Print full task state.
    """
    task_id = _get_flag(args, "--task")
    if not task_id:
        print("[ERROR] --task is required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # MAJOR-3

    data = _load_tasks()
    task = _find_task(data, task_id)
    if task is None:
        print("[WARN] Task '{}' not found.".format(task_id))
        sys.exit(0)

    print("\n--- Task Status: {} ---".format(task_id))
    print("  title:              {}".format(task.get("title", "")))
    print("  status:             {}".format(task.get("status", "")))
    print("  phase:              {}".format(task.get("phase", "(not set)")))
    print("  preferred_provider: {}".format(task.get("preferred_provider", "(not set)")))
    print("  complexity:         {}".format(task.get("complexity", "(not set)")))
    print("  assigned_to:        {}".format(task.get("assigned_to", "")))
    print("  claimed_at:         {}".format(task.get("claimed_at", "(not claimed)")))
    print("  completed_at:       {}".format(task.get("completed_at", "(not done)")))

    log = task.get("coordinator_log", [])
    if log:
        print("\n  coordinator_log:")
        for entry in log:
            print("    {}".format(entry))

    notes = task.get("notes", "") or ""
    if notes:
        print("\n  notes (truncated):")
        # Show first 300 chars safely
        safe_notes = notes[:300].encode("ascii", errors="replace").decode("ascii")
        print("    {}".format(safe_notes))
    print()


def cmd_list_mine(args):
    """
    List all in_progress tasks claimed by a specific model.
    """
    model = _get_flag(args, "--model")
    if not model:
        print("[ERROR] --model is required", file=sys.stderr)
        sys.exit(1)

    data = _load_tasks()
    # QA-5: Only check canonical "in_progress" — "in-progress" is normalised by _load_tasks().
    # The canonical status written by all current commands is "in_progress".
    mine = [
        t for t in data.get("tasks", [])
        if t.get("preferred_provider") == model
        and t.get("status") == "in_progress"
    ]

    if not mine:
        print("No in_progress tasks for model '{}'.".format(model))
        return

    print("In-progress tasks for {} ({}):\n".format(model, len(mine)))
    for t in mine:
        safe_title = t.get("title", "")[:70].encode("ascii", errors="replace").decode("ascii")
        print("  [{}]  phase={}  '{}'".format(
            t.get("task_id", "?"),
            t.get("phase", "-"),
            safe_title,
        ))
    print()


# ---------------------------------------------------------------------------
# Flag parser  (BUG-1 fix applied: same guard as checkpoint.py)
# ---------------------------------------------------------------------------

def _get_flag(args: list, flag: str, required: bool = True) -> str:
    """Return the value following *flag* in args list.

    Only returns the next token if it does NOT start with '--', preventing a
    flag name from being silently accepted as a value.
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
    "claim":         cmd_claim,
    "update":        cmd_update,
    "checkpoint":    cmd_checkpoint,
    "mark-tested":   cmd_mark_tested,
    "mark-done":     cmd_mark_done,
    "status":        cmd_status,
    "list-mine":     cmd_list_mine,
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
