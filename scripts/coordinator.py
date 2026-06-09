#!/usr/bin/env python3
"""
coordinator.py — Task lifecycle manager for multi-model AI agents.

Manages claim, track, checkpoint, and complete for tasks in active_tasks.json.
Integrates with task_tracker.py and checkpoint.py rather than duplicating logic.

Commands:
    python scripts/coordinator.py claim --task TASK_ID --model MODEL_NAME

    python scripts/coordinator.py update --task TASK_ID --phase PHASE \\
        [--note "optional note"]

    python scripts/coordinator.py checkpoint --task TASK_ID \\
        --done "what's been completed" \\
        --remaining "what's left" \\
        --next "exact next step"

    python scripts/coordinator.py complete --task TASK_ID \\
        --result-path path/to/output.md

    python scripts/coordinator.py status --task TASK_ID

    python scripts/coordinator.py list-mine --model MODEL_NAME

Models: claude-code, codex, antigravity
"""

import json
import re
import sys
import os
import subprocess
from datetime import datetime
from pathlib import Path

ROOT          = Path(__file__).resolve().parent.parent
TASKS_FILE    = ROOT / "tasks" / "active_tasks.json"
CHECKPOINT_PY = Path(__file__).resolve().parent / "checkpoint.py"


# ---------------------------------------------------------------------------
# Active tasks I/O
# ---------------------------------------------------------------------------

def _load_tasks() -> dict:
    try:
        return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("[ERROR] tasks/active_tasks.json not found: {}".format(TASKS_FILE), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print("[ERROR] JSON parse error: {}".format(exc), file=sys.stderr)
        sys.exit(1)


def _save_tasks(data: dict):
    # MAJOR-1: atomic write — prevents corruption on interrupted write
    tmp = TASKS_FILE.with_suffix('.tmp')
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(tmp, TASKS_FILE)


def _find_task(data: dict, task_id: str) -> dict:
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
# External script caller
# ---------------------------------------------------------------------------

def _run_checkpoint(extra_args: list) -> int:
    """Call checkpoint.py with the given args. Returns returncode."""
    result = subprocess.run(
        [sys.executable, str(CHECKPOINT_PY)] + extra_args,
        capture_output=False,
    )
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

    rc = _run_checkpoint(checkpoint_args)
    if rc == 0:
        # Also update phase in tasks file
        data = _load_tasks()
        task = _find_task(data, task_id)
        if task:
            task["phase"] = "checkpointed"
            _append_log_entry(task, "CHECKPOINTED interrupted_by={}".format(interrupted_by))
            _save_tasks(data)
    else:
        sys.exit(rc)


def cmd_complete(args):
    """
    Mark task as tested, phase=done, write result path.
    """
    task_id     = _get_flag(args, "--task")
    result_path = _get_flag(args, "--result-path", required=False) or ""

    if not task_id:
        print("[ERROR] --task is required", file=sys.stderr)
        sys.exit(1)
    _validate_task_id(task_id)  # MAJOR-3

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

    _append_log_entry(task, "COMPLETE result={}".format(result_path or "(no path)"))

    _save_tasks(data)
    print("[OK] Task '{}' marked complete (status=tested, phase=done)".format(task_id))
    if result_path:
        print("     Result path: {}".format(result_path))

    # Remove from resume queue if it was checkpointed
    try:
        rc = _run_checkpoint(["mark-resumed", "--task", task_id])
    except Exception:
        pass  # Non-fatal — task may not have been in queue


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
    mine = [
        t for t in data.get("tasks", [])
        if t.get("preferred_provider") == model
        and t.get("status") in ("in_progress", "in-progress")
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
# Flag parser
# ---------------------------------------------------------------------------

def _get_flag(args: list, flag: str, required: bool = True) -> str:
    """Return the value following `flag` in args list."""
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            val = args[idx + 1]
            # Allow multi-word values up to next --flag
            return val
    if required:
        print("[ERROR] {} is required".format(flag), file=sys.stderr)
        sys.exit(1)
    return ""


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

COMMANDS = {
    "claim":         cmd_claim,
    "update":        cmd_update,
    "checkpoint":    cmd_checkpoint,
    "complete":      cmd_complete,
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
