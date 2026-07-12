#!/usr/bin/env python3
"""
INFRA-004 — SessionEnd Review Gate
Fires on Claude Code Stop hook. Checks for orphaned in-progress tasks and
unconfirmed QA signoffs. Returns JSON decision to Claude Code.

Exit 0 = allow session end.
Exit 1 = block session end (Claude Code treats non-zero as a block signal).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

# ── Windows UTF-8 safety (emoji in blocked-path output) ───────────────────────
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = REPO_ROOT / "tasks" / "active_tasks.json"
SESSION_ENV = REPO_ROOT / ".claude" / "session.env"
RESUME_SCRIPT = REPO_ROOT / "scripts" / "resume_candidate.py"

DIVIDER = "─" * 44


def read_session_id() -> Optional[str]:
    """Read session_id from .claude/session.env if it exists."""
    if SESSION_ENV.exists():
        try:
            for line in SESSION_ENV.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("SESSION_ID="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return None


def load_tasks() -> List[Dict[str, Any]]:
    """Load tasks from active_tasks.json. Return empty list on error."""
    if not TASKS_FILE.exists():
        return []
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        return data.get("tasks", [])
    except json.JSONDecodeError:
        print(
            "WARNING: active_tasks.json is not valid JSON — gate cannot check task state",
            file=sys.stderr,
        )
        return []
    except (KeyError, OSError):
        return []


def snapshot_task(task_id: str) -> None:
    """Call resume_candidate.py snapshot for a given task_id (best-effort)."""
    if not RESUME_SCRIPT.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(RESUME_SCRIPT), "snapshot", "--task-id", task_id],
            timeout=5,
            capture_output=True,
        )
    except Exception:
        pass  # snapshot is best-effort; never block on its failure


def main() -> int:
    # ── Bypass: /close skill sets this env var ────────────────────────────────
    if os.environ.get("CLAUDE_SESSION_CLOSING") == "1":
        return 0

    tasks = load_tasks()
    if not tasks:
        return 0

    session_id = read_session_id()  # None means "treat all in-progress as current"

    # ── Check 1: in-progress tasks for this session ───────────────────────────
    orphaned: List[Dict[str, Any]] = []
    for task in tasks:
        if task.get("status") != "in-progress":
            continue
        task_session = task.get("session_id")
        if session_id is None or task_session == session_id:
            orphaned.append(task)

    # ── Check 2: tested tasks with no QA signoff ──────────────────────────────
    unsigned: List[Dict[str, Any]] = []
    for task in tasks:
        if task.get("status") != "tested":
            continue
        tested_by = task.get("tested_by", "")
        if not tested_by or tested_by.strip() in ("", "—", "-", "null"):
            unsigned.append(task)

    # ── Snapshot in-progress tasks before potentially blocking ────────────────
    for task in orphaned:
        snapshot_task(task["task_id"])

    # ── INFRA-008: Worktree orphan count (informational — never blocks) ────────
    try:
        wt_result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "worktree_manager.py"), "audit"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO_ROOT)
        )
        for line in wt_result.stdout.splitlines():
            if "Orphaned/stale:" in line:
                print(f"[worktree-audit] {line.strip()}")
                break
    except Exception:
        pass  # worktree audit is informational; never block on its failure

    # ── Clean pass ────────────────────────────────────────────────────────────
    if not orphaned and not unsigned:
        return 0

    # ── Build violation report ────────────────────────────────────────────────
    lines: List[str] = []
    lines.append("")
    lines.append("⚠️  SESSION END BLOCKED")
    lines.append(DIVIDER)

    if orphaned:
        lines.append(f"{len(orphaned)} task(s) still in-progress this session:")
        for t in orphaned:
            tid = t.get("task_id", "?")
            agent = t.get("assigned_to", "?")
            phase = t.get("phase", "?")
            lines.append(f"  • {tid} ({agent}) — phase: {phase}")

    if unsigned:
        if orphaned:
            lines.append("")
        lines.append(f"{len(unsigned)} task(s) in Tested with no QA signoff:")
        for t in unsigned:
            tid = t.get("task_id", "?")
            lines.append(f"  • {tid} — tested_by is empty")

    lines.append("")
    lines.append("Run /close to write handoff and bypass this gate, or finish the tasks first.")
    lines.append(DIVIDER)
    lines.append("")

    report = "\n".join(lines)
    reason = (
        f"{len(orphaned)} in-progress task(s); {len(unsigned)} unsigned QA task(s). "
        "Run /close to bypass."
    )

    print(report)
    print(json.dumps({"decision": "block", "reason": reason}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
