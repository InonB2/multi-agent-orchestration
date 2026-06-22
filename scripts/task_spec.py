#!/usr/bin/env python3
"""
task_spec.py — Enforce pre-task specs for M/L/XL tasks before they start.

A spec captures everything needed for any model (or future session) to continue
the task cold: what is done, what remains, exact next step, and acceptance criteria.

Usage:
    python scripts/task_spec.py create --task TASK_ID
      -> interactive prompts for what's done, remaining, next step, acceptance criteria
    python scripts/task_spec.py create --task TASK_ID --done "..." --remaining "..." --next "..." --criteria "..."
      -> non-interactive / flag-driven
    python scripts/task_spec.py read     --task TASK_ID
    python scripts/task_spec.py validate --task TASK_ID
    python scripts/task_spec.py list-missing
      -> lists M/L/XL tasks that have no spec yet (excludes done/cancelled/deferred/pending-owner)
"""

# BUG-2: Python 3.8 compatibility — enables 'dict | None' and 'list[str]' annotations
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT       = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "tasks" / "active_tasks.json"
SPECS_DIR  = ROOT / "tasks" / "specs"

REQUIRED_FIELDS = [
    "task_id",
    "title",
    "complexity",
    "created_at",
    "created_by",
    "what_is_done",
    "what_remains",
    "exact_next_step",
    "acceptance_criteria",
    "assigned_to",
    "spec_version",
]

REQUIRED_CONTENT_FIELDS = [
    "what_is_done",
    "what_remains",
    "exact_next_step",
    "acceptance_criteria",
]

COMPLEX_SIZES          = {"M", "L", "XL"}
EXCLUDED_STATUSES      = {"done", "cancelled", "deferred", "pending-owner"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_tasks() -> dict:
    try:
        return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("[ERROR] tasks/active_tasks.json not found at {}.".format(TASKS_FILE), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print("[ERROR] Failed to parse tasks/active_tasks.json: {}".format(exc), file=sys.stderr)
        sys.exit(1)


def find_task(task_id: str) -> dict | None:
    data = load_tasks()
    for task in data.get("tasks", []):
        if task.get("task_id") == task_id:
            return task
    return None


def spec_path(task_id: str) -> Path:
    """Return the spec file path for *task_id*, rejecting path-traversal attempts."""
    candidate = (SPECS_DIR / "{}.json".format(task_id)).resolve()
    try:
        candidate.relative_to(SPECS_DIR.resolve())
    except ValueError:
        print("[ERROR] Invalid task ID — path traversal detected.", file=sys.stderr)
        sys.exit(1)
    return candidate


def parse_criteria(raw: str) -> list[str]:
    """Parse acceptance criteria from a JSON list string or comma-separated string."""
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [c.strip() for c in raw.split(",") if c.strip()]


def spec_required_for_complexity(complexity: str) -> bool:
    """True when *complexity* requires a pre-task spec at runtime."""
    return (complexity or "").upper() in COMPLEX_SIZES


def spec_validation_errors(task_id: str) -> list[str]:
    """Return spec validation errors for *task_id* without exiting."""
    dest = spec_path(task_id)

    if not dest.exists():
        return ["No spec file found for task '{}'.".format(task_id)]

    try:
        spec = json.loads(dest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ["Spec file is not valid JSON: {}".format(exc)]

    errors = []

    for field in REQUIRED_FIELDS:
        if field not in spec:
            errors.append("Missing required field: '{}'".format(field))

    for field in REQUIRED_CONTENT_FIELDS:
        val = spec.get(field)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            errors.append("Field '{}' is empty.".format(field))
        elif isinstance(val, list) and len(val) == 0:
            errors.append("Field '{}' (list) is empty.".format(field))

    return errors


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_create(args):
    task_id = args.task
    task = find_task(task_id)
    if task is None:
        print("[ERROR] Task '{}' not found in active_tasks.json.".format(task_id), file=sys.stderr)
        sys.exit(1)

    title       = task.get("title", "")
    complexity  = task.get("complexity", "")
    assigned_to = task.get("assigned_to", "")
    created_by  = args.created_by or "andy"

    # Non-interactive path: all flags provided
    if args.done and args.remaining and args.next and args.criteria:
        what_is_done        = args.done
        what_remains        = args.remaining
        exact_next_step     = args.next
        acceptance_criteria = parse_criteria(args.criteria)
    else:
        # Interactive path
        print("Creating spec for: {} — {}".format(task_id, title))
        print("(Leave blank to set an empty value)\n")
        what_is_done        = input("What is done so far?                     ").strip()
        what_remains        = input("What remains?                            ").strip()
        exact_next_step     = input("Exact next step?                         ").strip()
        criteria_raw        = input("Acceptance criteria (comma-separated):   ").strip()
        acceptance_criteria = parse_criteria(criteria_raw)

    spec = {
        "task_id":             task_id,
        "title":               title,
        "complexity":          complexity,
        "created_at":          datetime.now(timezone.utc).isoformat(),
        "created_by":          created_by,
        "what_is_done":        what_is_done,
        "what_remains":        what_remains,
        "exact_next_step":     exact_next_step,
        "acceptance_criteria": acceptance_criteria,
        "assigned_to":         assigned_to,
        "spec_version":        1,
    }

    SPECS_DIR.mkdir(parents=True, exist_ok=True)
    dest = spec_path(task_id)
    # Atomic write — prevents file corruption on interrupted write (MINOR-2)
    tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, dest)
    print("[OK] Spec written: {}".format(dest))


def cmd_read(args):
    task_id = args.task
    dest = spec_path(task_id)
    if not dest.exists():
        print("[ERROR] Spec file not found: {}".format(dest), file=sys.stderr)
        sys.exit(1)

    spec = json.loads(dest.read_text(encoding="utf-8"))
    print(json.dumps(spec, indent=2, ensure_ascii=False))


def cmd_validate(args):
    task_id = args.task
    errors = spec_validation_errors(task_id)

    if errors:
        print("[FAIL] Spec validation errors for '{}':\n".format(task_id))
        for err in errors:
            print("  - {}".format(err))
        sys.exit(2)

    spec = json.loads(spec_path(task_id).read_text(encoding="utf-8"))
    print("[PASS] Spec for '{}' is valid. ({} fields present)".format(
        task_id, len(spec)
    ))


def cmd_list_missing(args):
    data  = load_tasks()
    tasks = data.get("tasks", [])

    # Ensure specs dir exists so path checks don't raise
    SPECS_DIR.mkdir(parents=True, exist_ok=True)

    missing = []
    for task in tasks:
        status     = task.get("status", "")
        complexity = task.get("complexity", "")

        # Skip terminal/blocked statuses
        if status in EXCLUDED_STATUSES:
            continue

        # Only flag M / L / XL
        if complexity not in COMPLEX_SIZES:
            continue

        task_id = task.get("task_id", "")

        # EDGE-5: skip tasks with missing or empty task_id to avoid writing to '.json'
        if not task_id.strip():
            print("[WARN] Skipping task with missing task_id", file=sys.stderr)
            continue

        if not spec_path(task_id).exists():
            missing.append({
                "task_id":    task_id,
                "title":      task.get("title", "(no title)"),
                "status":     status,
                "complexity": complexity,
            })

    if not missing:
        print("All M/L/XL active tasks have specs. Nothing missing.")
        return

    # Column widths
    w_id  = max(len("TASK_ID"),    max(len(t["task_id"])         for t in missing))
    w_ttl = max(len("TITLE"),      max(len(t["title"][:52])      for t in missing))
    w_st  = max(len("STATUS"),     max(len(t["status"])          for t in missing))
    w_cpx = max(len("COMPLEXITY"), max(len(t["complexity"])      for t in missing))
    w_note = len("MISSING SPEC")

    sep = "  " + "-" * (w_id + w_ttl + w_st + w_cpx + w_note + 15)
    hdr = "  {:<{}} | {:<{}} | {:<{}} | {:<{}} | {}".format(
        "TASK_ID",    w_id,
        "TITLE",      w_ttl,
        "STATUS",     w_st,
        "COMPLEXITY", w_cpx,
        "NOTE",
    )
    print(hdr)
    print(sep)
    for t in missing:
        print("  {:<{}} | {:<{}} | {:<{}} | {:<{}} | MISSING SPEC".format(
            t["task_id"],          w_id,
            t["title"][:52],       w_ttl,
            t["status"],           w_st,
            t["complexity"],       w_cpx,
        ))

    print("\n{} task(s) flagged as missing a spec.".format(len(missing)))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="task_spec.py",
        description="Enforce pre-task specs for M/L/XL tasks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # create
    p_create = sub.add_parser("create", help="Create a spec file for a task.")
    p_create.add_argument("--task",       required=True, help="Task ID (must exist in active_tasks.json)")
    p_create.add_argument("--done",       default="",   help="What is done so far")
    p_create.add_argument("--remaining",  default="",   help="What remains")
    p_create.add_argument("--next",       default="",   help="Exact next step")
    p_create.add_argument("--criteria",   default="",   help="Acceptance criteria — JSON array or comma-separated")
    p_create.add_argument("--created-by", dest="created_by", default="andy",
                          help="Creator name/model (default: andy)")

    # read
    p_read = sub.add_parser("read", help="Print a spec file as JSON.")
    p_read.add_argument("--task", required=True, help="Task ID")

    # validate
    p_val = sub.add_parser("validate", help="Validate that a spec file has all required fields.")
    p_val.add_argument("--task", required=True, help="Task ID")

    # list-missing
    sub.add_parser(
        "list-missing",
        help="List M/L/XL active tasks that have no spec yet.",
    )

    args = parser.parse_args()

    dispatch = {
        "create":       cmd_create,
        "read":         cmd_read,
        "validate":     cmd_validate,
        "list-missing": cmd_list_missing,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
