"""
session.py — Session-Scoped Job Filtering utility (INFRA-002)

Commands:
    init        Generate + write SESSION_ID to .claude/session.env
    current     Print current SESSION_ID from .claude/session.env
    list-tasks  Print tasks matching current session_id
    orphaned    List tasks with no real session_id (e.g. pre-infra)

Usage:
    python scripts/session.py init
    python scripts/session.py current
    python scripts/session.py list-tasks
    python scripts/session.py orphaned
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (all relative to repo root — one level up from scripts/)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
SESSION_ENV_PATH = REPO_ROOT / ".claude" / "session.env"
ACTIVE_TASKS_PATH = REPO_ROOT / "tasks" / "active_tasks.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_session_id() -> str:
    """Return a session ID in the format sess-YYYYMMDD-HHMM."""
    now = datetime.now()
    return f"sess-{now.strftime('%Y%m%d-%H%M')}"


def _read_session_env() -> dict[str, str]:
    """Parse .claude/session.env into a dict. Returns {} if file missing."""
    if not SESSION_ENV_PATH.exists():
        return {}
    result: dict[str, str] = {}
    for line in SESSION_ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _write_session_env(data: dict[str, str]) -> None:
    """Write key=value pairs to .claude/session.env."""
    SESSION_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in data.items()]
    SESSION_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_tasks() -> list[dict]:
    """Load tasks from active_tasks.json. Returns empty list on error."""
    if not ACTIVE_TASKS_PATH.exists():
        return []
    with ACTIVE_TASKS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return data.get("tasks", [])


def _is_real_session_id(session_id: str) -> bool:
    """Return True if the session_id matches the sess-YYYYMMDD-HHMM pattern."""
    import re
    return bool(re.match(r"^sess-\d{8}-\d{4}$", str(session_id or "")))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init() -> None:
    """Generate a new session ID and write to .claude/session.env."""
    session_id = _generate_session_id()
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_session_env({
        "SESSION_ID": session_id,
        "SESSION_STARTED_AT": started_at,
    })
    print(f"Session initialized: {session_id}")
    print(f"Started at: {started_at}")
    print(f"Written to: {SESSION_ENV_PATH}")
    # Refresh product telemetry. Machine-specific quota/watchdog integrations are
    # deliberately not part of the public session lifecycle.
    try:
        import subprocess
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "telemetry_refresh.py")],
                           capture_output=True, text=True, timeout=30)
        if r.stdout.strip():
            print(r.stdout.strip())
    except Exception as exc:
        print(f"[warn] telemetry_refresh skipped: {exc}", file=sys.stderr)


def cmd_current() -> None:
    """Print the current SESSION_ID from .claude/session.env."""
    env = _read_session_env()
    session_id = env.get("SESSION_ID")
    if not session_id:
        print("ERROR: No active session. Run: python scripts/session.py init", file=sys.stderr)
        sys.exit(1)
    print(session_id)


def cmd_list_tasks() -> None:
    """Print tasks that belong to the current session."""
    env = _read_session_env()
    session_id = env.get("SESSION_ID")
    if not session_id:
        print("ERROR: No active session. Run: python scripts/session.py init", file=sys.stderr)
        sys.exit(1)

    tasks = _load_tasks()
    matching = [t for t in tasks if t.get("session_id") == session_id]

    if not matching:
        print(f"No tasks found for session: {session_id}")
        return

    print(f"Tasks for session {session_id} ({len(matching)} found):\n")
    for task in matching:
        status = task.get("status", "unknown")
        phase = task.get("phase", "—")
        assignee = task.get("assigned_to", "—")
        print(f"  [{task['task_id']}] {task.get('title', '(no title)')}")
        print(f"    status={status}  phase={phase}  assigned_to={assignee}")
    print()


def cmd_orphaned() -> None:
    """List tasks that have no real session ID (pre-infra or missing)."""
    tasks = _load_tasks()
    orphaned = [
        t for t in tasks
        if not _is_real_session_id(t.get("session_id", ""))
    ]

    if not orphaned:
        print("No orphaned tasks found.")
        return

    print(f"Orphaned tasks ({len(orphaned)} found — no valid sess-* session_id):\n")
    for task in orphaned:
        sid = task.get("session_id", "(none)")
        status = task.get("status", "unknown")
        print(f"  [{task['task_id']}] {task.get('title', '(no title)')}")
        print(f"    session_id={sid}  status={status}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

COMMANDS = {
    "init": cmd_init,
    "current": cmd_current,
    "list-tasks": cmd_list_tasks,
    "orphaned": cmd_orphaned,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python scripts/session.py <command>")
        print(f"Commands: {', '.join(COMMANDS)}")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
