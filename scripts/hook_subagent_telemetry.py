#!/usr/bin/env python3
"""hook_subagent_telemetry.py — INO-10 Gap B: register Claude Code Agent-tool
subagent dispatches (SubagentStart/SubagentStop hook events) with the same
dashboard/agent_activity telemetry that CLI-dispatched codex/agy tasks get
via dispatch_codex.ps1 / dispatch_agy.ps1 -> agent_telemetry.py.

WHY THIS EXISTS
----------------
Before this hook, only CLI-dispatched tasks (codex, agy via
scripts/dispatch_codex.ps1 / scripts/dispatch_agy.ps1) called
scripts/agent_telemetry.py start/stop, so the dashboard showed no activity
for Claude's own built-in Agent-tool subagents. It ensures Agent-tool activity
is tracked consistently with CLI dispatch activity and closes that gap
for Agent-tool subagents specifically.

HOW IT WORKS
------------
Registered as both a SubagentStart and a SubagentStop hook in
.claude/settings.json. Claude Code invokes this script with the hook's JSON
payload on STDIN (NOT as {{...}}-templated command-line args — see
scripts/rate_limit_watchdog.py's INO-10 Gap A comments for why that syntax
doesn't work). The payload schema (confirmed against the installed Claude
Code build's own hook schema):
    SubagentStart: {hook_event_name:"SubagentStart", agent_id, agent_type}
    SubagentStop:  {hook_event_name:"SubagentStop", agent_id, agent_type,
                     agent_transcript_path, stop_hook_active,
                     last_assistant_message}

This script maps that into an agent_telemetry.py start/stop call so the
dashboard shows "claude-<agent_type>-<short_id>" as a running entry for the
subagent's lifetime, same as codex-qa / agy-content entries.

FAIL-SAFE: this must NEVER block or fail the subagent it's instrumenting.
Every code path is wrapped; the script always exits 0.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TELEMETRY_PY = ROOT / "scripts" / "agent_telemetry.py"


def _agent_key(agent_type: str, agent_id: str) -> str:
    short_id = (agent_id or "")[:8] or "unknown"
    safe_type = (agent_type or "subagent").strip().lower().replace(" ", "-") or "subagent"
    return f"claude-{safe_type}-{short_id}"


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0  # never fail the subagent over a parse error

    event = payload.get("hook_event_name", "")
    agent_id = str(payload.get("agent_id", "") or "")
    agent_type = str(payload.get("agent_type", "") or "")
    if not agent_id or event not in ("SubagentStart", "SubagentStop"):
        return 0

    agent_key = _agent_key(agent_type, agent_id)

    try:
        if event == "SubagentStart":
            subprocess.run(
                [sys.executable, str(TELEMETRY_PY), "start",
                 "--agent", agent_key,
                 "--task", agent_id[:12],
                 "--desc", f"Agent-tool: {agent_type or 'subagent'}",
                 "--role", agent_type or "subagent",
                 "--reason", "dispatched via Agent tool (SubagentStart hook)"],
                capture_output=True, text=True, timeout=10,
            )
        elif event == "SubagentStop":
            subprocess.run(
                [sys.executable, str(TELEMETRY_PY), "stop",
                 "--agent", agent_key,
                 "--status", "done"],
                capture_output=True, text=True, timeout=10,
            )
    except Exception:
        pass  # best-effort telemetry; never block the subagent lifecycle

    return 0


if __name__ == "__main__":
    sys.exit(main())
