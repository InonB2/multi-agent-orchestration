"""
generate_handoff.py — Generate scratchpad/handoff_latest.md for Codex/Gemini fallback.

Also writes a canonical JSON bundle to:
  orchestration/handoffs/<handoff_id>/task-handoff.json
  orchestration/handoffs/latest/task-handoff.json  (copy of latest)

Captures: active/blocked tasks, recent session log tail, git diff stat, rate limit queue.
Run directly or called automatically by rate_limit_watchdog.py on_stop.
"""

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

ROOT              = Path(__file__).resolve().parent.parent
TASKS_FILE        = ROOT / "tasks" / "active_tasks.json"
SESSION_DIR       = ROOT / "session_logs"
QUEUE_FILE        = ROOT / "scratchpad" / "rate_limit_queue.json"
OUT_FILE          = ROOT / "scratchpad" / "handoff_latest.md"
HANDOFFS_DIR      = ROOT / "orchestration" / "handoffs"
CLAUDE_MD         = ROOT / "CLAUDE.md.template"
TAIL_LINES        = 50


def load_tasks():
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        tasks = data.get("tasks", [])
    except FileNotFoundError:
        return [], [], "tasks/active_tasks.json not found"
    except Exception as e:
        return [], [], f"Error reading tasks: {e}"

    in_progress = [t for t in tasks if t.get("status") in ("in_progress", "in-progress", "partial")]
    blocked     = [t for t in tasks if t.get("status") == "blocked"]
    return in_progress, blocked, None


def format_task_list(tasks, include_blocked_reason=False):
    if not tasks:
        return "_(none)_"
    lines = []
    for t in tasks:
        line = f"- **{t.get('task_id', '?')}** — {t.get('title', '(no title)')} [{t.get('assigned_to', '?')}]"
        desc = t.get("description") or t.get("notes")
        if desc:
            line += f"\n  {desc[:300]}"
        if include_blocked_reason and t.get("blocked_reason"):
            line += f"\n  Blocked: {t['blocked_reason']}"
        lines.append(line)
    return "\n".join(lines)


def latest_session_log():
    try:
        logs = sorted(SESSION_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime)
        if not logs:
            return "(no session logs found)"
        latest = logs[-1]
        lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-TAIL_LINES:] if len(lines) > TAIL_LINES else lines
        return f"_Source: {latest.name}_\n\n" + "\n".join(tail)
    except Exception as e:
        return f"Error reading session log: {e}"


def git_diff_stat():
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True, text=True, timeout=15, cwd=str(ROOT)
        )
        out = result.stdout.strip()
        return out if out else "No uncommitted changes"
    except Exception as e:
        return f"git diff failed: {e}"


def git_rev_parse(args):
    """Run git rev-parse with given args, return stripped stdout or empty string on error."""
    try:
        result = subprocess.run(
            ["git", "rev-parse"] + args,
            capture_output=True, text=True, timeout=15, cwd=str(ROOT)
        )
        return result.stdout.strip()
    except Exception:
        return ""


def git_diff_name_only():
    """Return list of files changed since HEAD."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, timeout=15, cwd=str(ROOT)
        )
        lines = [line for line in result.stdout.strip().splitlines() if line]
        return lines
    except Exception:
        return []


def rate_limit_queue():
    try:
        content = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        return "```json\n" + json.dumps(content, indent=2) + "\n```"
    except FileNotFoundError:
        return "No rate limit queue"
    except Exception as e:
        return f"Error reading rate limit queue: {e}"


def extract_coding_rules():
    """Extract the 'Your rules' and 'Team Quality Rubric' sections from CLAUDE.md."""
    try:
        text = CLAUDE_MD.read_text(encoding="utf-8", errors="replace")

        # Extract from "## Your rules" through the next "##" heading
        rules_match = re.search(
            r"(## Your rules\n.*?)(?=\n## |\Z)",
            text,
            re.DOTALL,
        )
        rubric_match = re.search(
            r"(## Team Quality Rubric\n.*?)(?=\n## |\Z)",
            text,
            re.DOTALL,
        )

        parts = []
        if rules_match:
            parts.append(rules_match.group(1).strip())
        if rubric_match:
            parts.append(rubric_match.group(1).strip())

        if parts:
            return "\n\n".join(parts)
        return "_(could not extract coding rules from CLAUDE.md)_"
    except FileNotFoundError:
        return "_(CLAUDE.md not found)_"
    except Exception as e:
        return f"_(error reading CLAUDE.md: {e})_"


def build_task_json_entry(task: dict, status_key: str) -> dict:
    """Convert a raw task dict into the JSON bundle task format."""
    notes = task.get("notes", "") or ""
    # Extract first sentence of notes as acceptance criteria (if any)
    acceptance_criteria = []
    if notes:
        first_sentence = notes.split(".")[0].strip()
        if first_sentence:
            acceptance_criteria = [first_sentence]

    return {
        "task_id": task.get("task_id", ""),
        "title": task.get("title", ""),
        "assigned_to": task.get("assigned_to", ""),
        "preferred_provider": "claude-code",
        "status": status_key,
        "acceptance_criteria": acceptance_criteria,
    }


def write_json_bundle(handoff_id: str, generated_at: str, in_progress: list, blocked: list):
    """Write orchestration/handoffs/<handoff_id>/task-handoff.json and copy to latest/."""
    commit_sha = git_rev_parse(["HEAD"])
    branch     = git_rev_parse(["--abbrev-ref", "HEAD"])
    relevant_files = git_diff_name_only()

    active_tasks_json  = [build_task_json_entry(t, "in_progress")  for t in in_progress]
    blocked_tasks_json = [build_task_json_entry(t, "blocked")       for t in blocked]

    bundle = {
        "handoff_id": handoff_id,
        "generated_at": generated_at,
        "locale": "en-US",
        "repo": {
            "root": str(ROOT).replace("\\", "/"),
            "commit_sha": commit_sha,
            "branch": branch,
        },
        "active_tasks": active_tasks_json,
        "blocked_tasks": blocked_tasks_json,
        "relevant_files": relevant_files,
        "validation": {
            "commands": [
                "python -m pytest",
                "python -m py_compile scripts/*.py",
            ],
            "required_checks": ["QA sign-off required before status=done"],
        },
        "context_files": [
            "PROTOCOL.md",
            "AGENTS.md",
            "CLAUDE.md.template",
            "scratchpad/handoff_latest.md",
        ],
        "instructions": (
            "Read PROTOCOL.md first, then scratchpad/handoff_latest.md. "
            "Claim in_progress tasks by setting preferred_provider to your tool name. "
            "When done: set status=tested."
        ),
    }

    # Write to versioned directory
    handoff_dir = HANDOFFS_DIR / handoff_id
    handoff_dir.mkdir(parents=True, exist_ok=True)
    json_path = handoff_dir / "task-handoff.json"
    json_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")

    # Overwrite latest/ with a fresh copy
    latest_dir = HANDOFFS_DIR / "latest"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(str(handoff_dir), str(latest_dir))

    return json_path


TASK_COMPLETION_RITUAL = """\
1. Mark task status → "tested" in tasks/active_tasks.json (never "done" directly)
2. Add tested_by field (leave blank — QA agent will fill it)
3. Log session findings to agents/learning_logs/[your-agent-name].md
4. Run `python scripts/generate_handoff.py` before closing session"""

SOP_QUICK_REFERENCE = """\
- Tool selection: read BKM/cli_first_policy.md
- DB changes: read BKM/sop_db_migrations.md
- Agent boundaries: read BKM/sop_agent_domain_boundaries.md
- Full index: BKM/INDEX.md"""


def main():
    now = datetime.now().astimezone()
    timestamp   = now.isoformat()
    handoff_id  = now.strftime("handoff-%Y%m%d-%H%M%S")
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%S")

    in_progress, blocked, task_error = load_tasks()

    rl_queue    = rate_limit_queue()
    session_log = latest_session_log()
    diff_stat   = git_diff_stat()

    in_progress_section = format_task_list(in_progress)
    blocked_section     = format_task_list(blocked, include_blocked_reason=True)

    if task_error:
        in_progress_section = f"_(error: {task_error})_"
        blocked_section     = f"_(error: {task_error})_"

    coding_rules   = extract_coding_rules()

    key_skills_parts = [
        "## Key Skills (embedded for portability)",
        "",
        "### Coding Rules (from CLAUDE.md)",
        coding_rules,
    ]
    key_skills_parts += [
        "",
        "### Task Completion Ritual",
        TASK_COMPLETION_RITUAL,
        "",
        "### SOP Quick Reference",
        SOP_QUICK_REFERENCE,
    ]

    key_skills_section = "\n".join(key_skills_parts)

    md = f"""# Handoff — {timestamp}

## Rate Limit Queue
{rl_queue}

## Active Tasks (In Progress)
{in_progress_section}

## Blocked Tasks
{blocked_section}

## Recent Session Log (last {TAIL_LINES} lines)
{session_log}

## Files Changed This Session
```
{diff_stat}
```

## What To Do Next
1. Read GEMINI.md or AGENTS.md in the project root for project rules and context
2. Continue the In Progress tasks listed above
3. When a task is done, update tasks/active_tasks.json (status → "tested", add tested_by field)
4. Log your session findings to agents/learning_logs/[your-name].md

---

{key_skills_section}
"""

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(md, encoding="utf-8")
    print(f"[OK] Handoff doc written: {OUT_FILE}")

    # Write JSON bundle
    if not task_error:
        json_path = write_json_bundle(handoff_id, generated_at, in_progress, blocked)
        print(f"[OK] JSON bundle written: {json_path}")
        print(f"[OK] Latest copy:         {HANDOFFS_DIR / 'latest' / 'task-handoff.json'}")
    else:
        print(f"[WARN] Skipping JSON bundle — task load error: {task_error}")


if __name__ == "__main__":
    main()
