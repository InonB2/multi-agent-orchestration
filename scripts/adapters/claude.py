"""Claude Code headless adapter."""

from __future__ import annotations

import json
from pathlib import Path

from adapters.base import DispatchRequest, Invocation


class Adapter:
    engine = "claude"

    def build(self, request: DispatchRequest, executable: str, scripts_dir: Path) -> Invocation:
        argv = [
            executable, "--print", "--output-format", "json",
            "--dangerously-skip-permissions", "--no-session-persistence",
        ]
        if not request.enable_mcp:
            argv += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
        if request.model:
            argv += ["--model", request.model]
        if request.effort:
            argv += ["--effort", request.effort]
        return Invocation(argv, request.prompt, argv, None)

    def parse(self, stdout: str, stderr: str) -> str:
        try:
            payload = json.loads(stdout)
        except (ValueError, TypeError):
            return stdout.strip()
        return str(payload.get("result") or "")

    def probe_prompt(self) -> tuple[str, str]:
        return ("Reply with exactly: AOA_CLAUDE_OK", "AOA_CLAUDE_OK")
