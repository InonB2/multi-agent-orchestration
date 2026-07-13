"""Codex CLI adapter: prompt on stdin, cwd via subprocess, MCP disabled."""

from __future__ import annotations

import json
from pathlib import Path

from adapters.base import DispatchRequest, Invocation


class Adapter:
    engine = "codex"

    def build(self, request: DispatchRequest, executable: str, scripts_dir: Path) -> Invocation:
        argv = [
            executable, "exec", "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
        ]
        if not request.enable_mcp:
            argv += ["-c", "mcp_servers={}"]
        if request.model:
            argv += ["--model", request.model]
        if request.effort:
            argv += ["-c", f'model_reasoning_effort="{request.effort}"']
        argv.append("-")
        return Invocation(argv, request.prompt, argv, None)

    def parse(self, stdout: str, stderr: str) -> str:
        message = ""
        for line in stdout.splitlines():
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            payload = item.get("item") or {}
            if item.get("type") == "item.completed" and payload.get("type") == "agent_message":
                message = str(payload.get("text") or "")
        return message

    def probe_prompt(self) -> tuple[str, str]:
        return ("Reply with exactly: AOA_CODEX_OK", "AOA_CODEX_OK")
