"""AGY adapter using the portable entry and ConPTY on Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from adapters.base import DispatchRequest, Invocation


class Adapter:
    engine = "agy"

    def build(self, request: DispatchRequest, executable: str, scripts_dir: Path) -> Invocation:
        argv = [
            sys.executable, str(scripts_dir / "agy_pty.py"),
            "--agy", executable, "--workdir", str(request.workdir),
            "--timeout", str(request.timeout),
        ]
        if request.model:
            argv += ["--model", request.model]
        env = os.environ.copy()
        env["TERM"] = "xterm"
        return Invocation(argv, request.prompt, argv, env)

    def parse(self, stdout: str, stderr: str) -> str:
        return stdout.strip()

    def probe_prompt(self) -> tuple[str, str]:
        return ("Reply with exactly: AOA_AGY_OK", "AOA_AGY_OK")
